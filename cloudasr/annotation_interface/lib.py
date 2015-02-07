import os
import re
import json
import uuid
import wave
import zmq.green as zmq
from schema import create_db_session, User, Recording, Hypothesis, Transcription
from cloudasr.messages.helpers import *

def create_recordings_saver(address, path, model):
    def create_socket():
        context = zmq.Context()
        socket = context.socket(zmq.PULL)
        socket.bind(address)

        return socket

    run_forever = lambda: True

    return RecordingsSaver(create_socket, model, run_forever)

def create_db_connection(connection_string):
    return create_db_session(connection_string)


class RecordingsSaver:

    def __init__(self, create_socket, model, should_continue):
        self.create_socket = create_socket
        self.model = model
        self.should_continue = should_continue

    def run(self):
        self.socket = self.create_socket()

        while self.should_continue():
            recording = parseSaverMessage(self.socket.recv())

            id = uniqId2Int(recording.id)
            model = recording.model
            body = recording.body
            frame_rate = recording.frame_rate
            alternatives = alternatives2List(recording.alternatives)

            self.model.save_recording(id, model, body, frame_rate, alternatives)


class RecordingsModel:

    def __init__(self, path, db):
        self.db = db
        self.path = path
        self.file_saver = FileSaver(path)

    def get_models(self):
        from sqlalchemy import func
        return self.db.query(Recording.model, func.count(Recording.id).label("count")) \
            .group_by(Recording.model) \
            .order_by(Recording.model.asc()) \
            .all()

    def get_recordings(self, model):
        return self.db.query(Recording).filter(Recording.model == model).all()

    def get_recording(self, id):
        return self.db.query(Recording).get(int(id))

    def get_random_recording(self):
        from sqlalchemy import func
        return self.db.query(Recording) \
            .order_by(Recording.rand_score) \
            .limit(1) \
            .one()

    def save_recording(self, id, model, body, frame_rate, alternatives):
        (path, url) = self.file_saver.save_wav(id, model, body, frame_rate)
        self.save_recording_to_db(id, model, path, url, alternatives)

    def save_recording_to_db(self, id, model, path, url, alternatives):
        recording = Recording(
            id = id,
            model = model,
            path = path,
            url = url,
            score = alternatives[0]["confidence"],
            rand_score = alternatives[0]["confidence"]
        )

        for alternative in alternatives:
            recording.hypotheses.append(Hypothesis(
                text = alternative["transcript"],
                confidence = alternative["confidence"]
            ))

        self.db.add(recording)
        self.db.commit()

    def add_transcription(self, user, id, transcription, native_speaker, offensive_language, not_a_speech):
        transcription = Transcription(
            user_id = user.get_id(),
            text = transcription,
            native_speaker = native_speaker,
            offensive_language = offensive_language,
            not_a_speech = not_a_speech
        )

        recording = self.get_recording(id)
        recording.transcriptions.append(transcription)
        recording.update_score()
        self.db.commit()


class FileSaver:

    def __init__(self, path):
        self.path = path

    def save_wav(self, id, model, body, frame_rate):
        path = '%s/%s-%d.wav' % (self.path, model, id)
        url = '/static/data/%s-%d.wav' % (model, id)

        wav = wave.open(path, 'w')
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(frame_rate)
        wav.writeframes(body)
        wav.close()

        return (path, url)


class UsersModel:

    def __init__(self, db):
        self.db = db

    def get_user(self, id):
        return self.db.query(User).get(int(id))

    def upsert_user(self, userinfo):
        user = self.get_user(userinfo['id'])

        if user:
            user.email = userinfo['email']
            user.name = userinfo['name']
            user.avatar = userinfo['picture']
        else:
            user = User(
                id = int(userinfo['id']),
                email = userinfo['email'],
                name = userinfo['name'],
                avatar = userinfo['picture']
            )

        self.db.add(user)
        self.db.commit()

        return user


