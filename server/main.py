import json
import datetime
import numpy as np
import pandas
import pandas as pd
import pymysql
import requests
from flask import Flask, request, jsonify, render_template, redirect, session, url_for
from flask_login import LoginManager, login_user, logout_user, login_required, current_user


import config
import os
import csv

# 추가
from keras.models import load_model
import torch
import pickle
from model import Model

#add
from collections import Counter
import tensorflow as tf

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/test.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 이진 분류 모델 파일 불러오기
with open('/home/ec2-user/environment/AiConan/model/data_scaler.pkl', 'rb') as f:
    scaler = pickle.load(f)  # data scaler
with open('/home/ec2-user/environment/AiConan/model/timestamp_scaler.pkl', 'rb') as f:
    time_scaler = pickle.load(f)  # Timestamp scaler
model = load_model('/home/ec2-user/environment/AiConan/model/binary_model.h5')

# 다중 분류 모델 파일 불러오기
with open('/home/ec2-user/environment/AiConan/model/data_scaler_mc.pkl', 'rb') as f:
    scaler_mc = pickle.load(f)  # data scaler
with open('/home/ec2-user/environment/AiConan/model/time_scaler_mc.pkl', 'rb') as f:
    time_scaler_mc = pickle.load(f)  # Timestamp scaler

model_mc = load_model('/home/ec2-user/environment/AiConan/model/model.h5')


mysql_conn = pymysql.connect(
    host=os.environ.get("DB_HOST"),
    user=os.environ.get("DB_USER"),
    password=os.environ.get("DB_PASSWORD"),
    db=os.environ.get("DB_NAME"),
    port=3306,
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)
cursor = mysql_conn.cursor()

app.secret_key = os.environ.get("APP_SECRET_KEY", "default_secret_key")

# define login page endpoint
@app.route('/auth')
def login():
    return render_template('login.html')

import secrets
# define authentication endpoint
@app.route('/authenticate', methods=['POST'])
def authenticate():
    userId = request.form['userId']
    password = request.form['password']
    print(f">>> user id : {userId}, user pwd : {password}")
    
    # check if user exists in the database
    cursor.execute("SELECT id FROM admin WHERE userId = %s AND password = %s", (userId, password))
    admin = cursor.fetchone()

     # if user exists, create a new authentication token and return it as a JSON response
    import uuid
    if admin is not None:
        token = str(uuid.uuid4())
        session['admin'] = admin['id']
        print(">>> admin login")
        return jsonify({'token': token})
    else:
        return jsonify({'error': 'Invalid user ID or password.'}), 401

# define logout endpoint
@app.route('/logout')
def logout():
    session.pop('admin', None)
    return jsonify({'message': 'Logout successful'})


# communicate with web
@app.route('/api/detection', methods=["POST"])
def detect():
    noa = 0  # # of attack
    

    data = request.files['file']
    username = request.form['username']
    # print('>>',username)


    #   Data preprocessing
    csv_data = pd.read_csv(data)
    df_row = pd.DataFrame(csv_data, columns=csv_data.columns)

    if 'Unnamed: 0' in df_row.columns:
            df_row.drop(columns='Unnamed: 0', axis=1,inplace=True)

    np_data = data_transform_for_detection(df_row)
    resp = dict()

    #   Attack detection using AI model
    result = model_detection(np_data)  # binary classification using AI 0: normal 1:  attack
    noa = Counter(result.round().tolist())[1.0]
    print('>>binary_model res:',Counter(result.round().tolist()))
    #   Send data to classification model with index, timestamp, data, username
    index = np.where(result.round() == 1)[0]

    if len(index) > 0:  # 공격 데이터가 있다는 가정하에만 해놓은 거고, 공격 데이터가 아예 없을 떄도 필요
        json_data = {'index': index, 'origin_data': df_row.iloc[index,:], 'user': username}
        # json_data = json.dumps(json_data)


        save(json_data)
        
        normal_df = df_row.drop(index)
        print(Counter(normal_df['Label'].to_numpy().tolist()))
        insert(normal_df)
        # response = requests.post(url + "/data", json=json_data)

    else:
        print('>>df???',type(df_row))
        insert(df_row)

    #   Data 전송 비동기 처리 시, json_data 사용하면 됨.
    resp['numberOfAttack'] = noa
    app.logger.info('binary classification success')
    # 응답 처리 코드
    return jsonify(json.dumps(resp)), 200

def save(data):
    if request.is_json:
        data = request.get_json()

    np_data = data_transform_for_classification(data['origin_data'])
    result = model_classification(np_data)  # need to erase np_data timestamp np.delete(np_data,0,axis=1)
    print('>>>classification',Counter(result.tolist()))     # for check # of classified attack
    print(type(result))
    data['origin_data'].loc[:,'Label']= result
    print(data['origin_data']['Label'].value_counts())
    # print(data['origin_data'])
    db_res = insert(data['origin_data'])

    if db_res == 'Success':
        app.logger.info('db update success')

    # 응답 처리 코드
    return 200


def model_detection(data):
    threshold = 0.9634705409763548

    # attack detection using anomaly detection AI model
    res = model(data)
    mse = np.mean(np.power(data - res, 2), axis=1)
    y_pred = np.where(mse > threshold * 0.1, 1, 0)
    is_attack = np.mean(y_pred, axis=1)
    return is_attack


def model_classification(data):
    which_attack = model_mc.predict(data)
    return np.argmax(which_attack,axis=1)

def data_transform_for_classification(data):
    if 'Label' in data.columns:
        data = data.drop(columns='Label', axis=1)
        
    data_df = data.reindex(columns=['Timestamp', 'CAN ID', 'DLC', 'Data1', 'Data2', 'Data3', 'Data4', 'Data5', 'Data6', 'Data7', 'Data8'])

    # Timestamp scaling
    timestamp = data_df['Timestamp']
    timestamp_data = data_df['Timestamp'].values.reshape(-1, 1)
    scaled_timestamp_data = time_scaler_mc.transform(timestamp_data)

    # Data scaling
    cols_to_scale = ['DLC','Data1', 'Data2', 'Data3', 'Data4', 'Data5', 'Data6', 'Data7', 'Data8']
    data_df[cols_to_scale] = scaler_mc.transform(data_df[cols_to_scale])

    # 변환된 데이터를 다시 데이터프레임에 반영
    data_df['scaled_timestamp'] = scaled_timestamp_data.flatten()

    data_df = data_df.drop(columns='Timestamp', axis=1)

    data_df = data_df.reindex(
        columns=['scaled_timestamp', 'CAN ID', 'DLC', 'Data1', 'Data2', 'Data3', 'Data4', 'Data5', 'Data6', 'Data7', 'Data8'])

    # before_expand_df = data_df
    # 차원 변환
    data_df = np.expand_dims(data_df, axis=-1)
    data_df = np.reshape(data_df, (data_df.shape[0], 1, data_df.shape[1]))

    return data_df

# if get data file from Spring. it makes data useful to model
def data_transform_for_detection(data):

    
    if 'Label' in data.columns:
        data = data.drop(columns='Label', axis=1)
        
    data_df = data.reindex(columns=['Timestamp', 'CAN ID', 'DLC', 'Data1', 'Data2', 'Data3', 'Data4', 'Data5', 'Data6', 'Data7', 'Data8'])

    # Timestamp scaling
    timestamp = data_df['Timestamp']
    timestamp_data = data_df['Timestamp'].values.reshape(-1, 1)
    scaled_timestamp_data = time_scaler.transform(timestamp_data)

    # Data scaling
    cols_to_scale = ['DLC','Data1', 'Data2', 'Data3', 'Data4', 'Data5', 'Data6', 'Data7', 'Data8']
    data_df[cols_to_scale] = scaler.transform(data_df[cols_to_scale])

    # 변환된 데이터를 다시 데이터프레임에 반영
    data_df['scaled_timestamp'] = scaled_timestamp_data.flatten()

    data_df = data_df.drop(columns='Timestamp', axis=1)

    data_df = data_df.reindex(
        columns=['scaled_timestamp', 'CAN ID', 'DLC', 'Data1', 'Data2', 'Data3', 'Data4', 'Data5', 'Data6', 'Data7', 'Data8'])

    # before_expand_df = data_df
    # 차원 변환
    data_df = np.expand_dims(data_df, axis=-1)
    data_df = np.reshape(data_df, (data_df.shape[0], 1, data_df.shape[1]))

    return data_df


# make connection with AWS RDS DB
# def create_app(test_config=None):
#     if test_config:
#         app.config.from_object(config)
#     else:
#         app.config.update(test_config)

#     db.init_app(app)

def insert(data):
    # data is an array of JSON objects, each containing the following keys: 
    # 'timestamp', 'ID', 'DLC', 'data', 'attack'
    app.logger.info('save data to DB')

    print('??data??',type(data))
    # Build a list of tuples, each representing a row to be inserted into the database
    rows_to_insert = []
    for index, row in data.iterrows():
        # print(">> ?? >>", type(row))
        data_string =  str(row['Data1']) + str(row['Data2'])+ str(row['Data3'])+ str(row['Data4'])+str(row['Data5'])+\
            str(row['Data6'])+\
            str(row['Data7'])+\
            str(row['Data8'])
        attack_type = 1 if int(row['Label']) == 0 else 2 if int(row['Label']) == 4 else 3 if int(row['Label']) == 3 else 4
        row_tuple = (
            str(int(8)),
            str(row['CAN ID']),
            data_string,
            float(row['Timestamp']),
            attack_type
        )
        rows_to_insert.append(row_tuple)

    # Execute a batch insert query to insert all rows at once
    query = "INSERT INTO abnormal_packets (dlc, can_net_id, data, timestamp, attack_types_id) VALUES (%s, %s, %s, %s, %s)"
    result = cursor.executemany(query, rows_to_insert)
    print('>> run?')
    # Commit the changes to the database
    mysql_conn.commit()

    return 'Success'

@app.route('/api/data', methods=["GET"])
def getData():
    cursor.execute("SELECT * FROM abnormal_packets;")
    data = cursor.fetchall()
    cursor.close()
    return jsonify(data)
    

if __name__ == '__main__':
    # create_app().run('0.0.0.0', port=8000, debug=True)
    app.run('0.0.0.0', port=8000, debug=True)