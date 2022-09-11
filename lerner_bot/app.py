import os
import json
import boto3
import requests
import psycopg2
from psycopg2 import Error
from datetime import datetime

REGION = os.environ['AWS_REGION']
TELE_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_API_URL = "https://api.telegram.org/bot{}/".format(TELE_TOKEN)
DB_CONN_USER = os.environ['DB_CONN_USER']
DB_CONN_PASS = os.environ['DB_CONN_PASS']
DB_CONN_HOST = os.environ['DB_CONN_HOST']
DB_CONN_PORT = os.environ['DB_CONN_PORT']
DB_CONN_DB_NAME = os.environ['DB_CONN_DB_NAME']

TRANSLATOR = boto3.client(service_name='translate', region_name=REGION, use_ssl=True)
SOURCE_LANG = 'de'
TARGET_LANG = 'en'

# I want to use a single connection per invocation, so I will keep a global conn
DB_CONN = None

def open_db_connection():
    global DB_CONN
    
    if DB_CONN is not None:
        return DB_CONN
    
    try:
        DB_CONN = psycopg2.connect(user = DB_CONN_USER,
                                  password = DB_CONN_PASS,
                                  host = DB_CONN_HOST,
                                  port = DB_CONN_PORT,
                                  database = DB_CONN_DB_NAME)
                                  
        # I guess this is the easiest way to deal with multiple transactions in
        # a single connection
        DB_CONN.set_session(autocommit=True)
        
        return DB_CONN 
    except (Exception, psycopg2.DatabaseError) as error :
        print ("Error while connecting to PostgreSQL!", error)
        raise error
        
def close_db_connection():      
    global DB_CONN
    
    if DB_CONN is not None:
        DB_CONN.close()
        DB_CONN = None
    
def get_random_sentence():
    db_conn = open_db_connection()
    
    cursor = db_conn.cursor()
    query = "select s.deu_id, s.deu_text, s.spa_id, s.spa_text from sentences s order by random() limit 1"
    
    cursor.execute(query)
    result = cursor.fetchall() 
    
    return result[0][1]
    
def get_random_word_to_learn(user_id):
    db_conn = open_db_connection()
    
    cursor = db_conn.cursor()
    query = """
    SELECT word 
    FROM learn_words 
    WHERE user_id = %s 
    ORDER BY random() 
    LIMIT 1
    """

    cursor.execute(query, (user_id,))
    result = cursor.fetchall() 
    
    return result[0][0] if len(result) > 0 else ""    

def get_sentence_with(word):
    db_conn = open_db_connection()
    
    cursor = db_conn.cursor()
    query = """
        SELECT s.deu_id, s.deu_text, s.spa_id, s.spa_text 
        FROM sentences s 
        WHERE s.deu_text ILIKE %s 
        ORDER BY random() 
        LIMIT 1
        """

    cursor.execute(query, ('%'+ word +'%',))
    result = cursor.fetchall() 
    
    sentence = result[0][1] if len(result) > 0 else "sentence not found :("
    
    return sentence
    
def get_latest_chat_message(chat_id): 
    db_conn = open_db_connection()
    
    cursor = db_conn.cursor()
    query = """select m.message_id, m.message_date, m.message_text, m.reply_text 
        from chat_messages m 
        where m.chat_id = %s 
        order by message_date DESC 
        limit 1"""

    cursor.execute(query, (chat_id,))
    result = cursor.fetchall() 
    
    if len(result) > 0:
        first_row = result[0]
        latest_chat_message = {
            'message_id': first_row[0], 
            'message_date': first_row[1], 
            'message_text': first_row[2],
            'reply_text': first_row[3]
        }
    else:
        latest_chat_message = {
            'message_id': '', 
            'message_date': '', 
            'message_text': '',
            'reply_text': ''
        }
    
    return latest_chat_message

def store_word_to_learn(user_id, word_to_learn):
    db_conn = open_db_connection()
    cursor = db_conn.cursor()
    
    cursor.execute(
        """INSERT INTO learn_words (user_id, word, creation_date, last_use_date, use_counter) 
        VALUES(%s, %s, %s,  %s, %s)""", 
        (user_id, word_to_learn, datetime.now(), datetime.now(), 0)
    )
    
    cursor.close()
    
def store_user_sentence(user_id, sentence):    
    db_conn = open_db_connection()
    cursor = db_conn.cursor()
    
    cursor.execute(
        """INSERT INTO sentences (src, user_id, deu_text, creation_date) 
        VALUES(%s, %s, %s,  %s)""", 
        ("bot_user",user_id, sentence, datetime.now())
    )
    
    cursor.close()
    
def store_chat_message(user_id, chat_id, message_id, message, reply):
    try:
        db_conn = open_db_connection()
        cursor = db_conn.cursor()
    
        print("Inserting into chat_messages", user_id, chat_id, message_id, message, reply)
        cursor.execute(
            """INSERT INTO chat_messages (user_id, chat_id, message_id, message_text, reply_text) 
            VALUES(%s, %s, %s, %s, %s)""", 
            (user_id, chat_id, message_id, message, reply)
        )
        cursor.close()
    except Error as error:    
        print('Failed to store chat message')
        print(error)


def send_message(text, chat_id):
    url = TELEGRAM_API_URL + "sendMessage?text={}&chat_id={}".format(text, chat_id)
    requests.get(url)
    
def translate(text, from_l=SOURCE_LANG, into_l=TARGET_LANG):
    return TRANSLATOR.translate_text(
        Text=text, 
        SourceLanguageCode=from_l, 
        TargetLanguageCode=into_l)
    
def handle_random_sentence_command(event, message):
    user_id = message['from']['id']
    message_text = message['text']
    
    # get rid of the command part in the user message:
    word_to_learn = message_text.replace('/satz', '', 1).strip()
    
    word = word_to_learn if len(word_to_learn) > 0 else get_random_word_to_learn(user_id)
    
    final_text = get_sentence_with(word)
    return final_text

def handle_translate_command(event, message):
    chat_id = message['chat']['id']
    message_text = message['text']
    
    # get rid of the command part in the user message:
    message_text = message_text.replace('/t', '', 1).strip()
    
    # If the user did not send an explicit message to translate,
    # we will take the previous reply we gave them and translate that
    to_translate = message_text if len(message_text) > 0 else get_latest_chat_message(chat_id)['reply_text']

    result = translate(to_translate)
    return result['TranslatedText']
    
def handle_learn_word_command(event, message):    
    user_id = message['from']['id']
    message_text = message['text']
    
    # get rid of the command part in the user message:
    word_to_learn = message_text.replace('/lerne', '', 1).strip()
    
    # we want non empty single word, otherwise refuse to store this
    if len(word_to_learn) == 0 or ' ' in word_to_learn:
        return 'I don\'t think it is a good idea to learn "'+word_to_learn+'"'
    
    store_word_to_learn(user_id, word_to_learn)
    return 'Stored: "'+word_to_learn+'"'
    
def handle_store_sentence_command(event, message):
    user_id = message['from']['id']
    message_text = message['text']
    
    # get rid of the command part in the user message:
    new_sentence = message_text.replace('/as', '', 1).strip()  
    
    # we want non empty sentence
    if len(new_sentence) == 0:
        return 'I don\'t think it is a good idea to add sentence "'+new_sentence+'"'
        
    store_user_sentence(user_id, new_sentence)
    return 'Stored: "'+new_sentence+'"'    

def parse_command(message_text):
    if message_text.startswith( '/t' ):
        return 1
    elif message_text.startswith( '/lerne' ):
        return 2
    if message_text.startswith( '/satz' ):
        return 3  
    if message_text.startswith( '/as' ):
        return 4      
    else:
        return 0
    
COMMAND_MAP = {
    0 : handle_random_sentence_command, # 0 is the default when no command was found
    1 : handle_translate_command,
    2 : handle_learn_word_command,
    3 : handle_random_sentence_command,
    4 : handle_store_sentence_command,
}    
    
def lambda_handler(event, context):
    # print('## ENVIRONMENT VARIABLES')
    # print(os.environ)
    print('## EVENT')
    print(event)
    
    open_db_connection()

    body = json.loads(event['body'])
    
    message_key = 'message' if 'message' in body else 'edited_message'
    
    update_id = body['update_id']
    message = body[message_key]
    
    chat_id = message['chat']['id']
    user_id = message['from']['id']
    message_text = message['text']
    
    command_num = parse_command(message_text)
    
    reply = COMMAND_MAP[command_num](event, message)
    
    send_message(reply, chat_id)
    store_chat_message(user_id, chat_id, update_id, message_text, reply)
    
    close_db_connection()
    
    return {
        'statusCode': 200
    }
