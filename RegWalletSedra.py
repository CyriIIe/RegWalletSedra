import sqlite3
import telebot
from telebot.types import Message
import subprocess
import json
import os
import logging
import time

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Remplacez TOKEN par le token de votre bot Telegram
TOKEN = "BOT_TOKEN"
GROUP_CHAT_ID = CHAT_ID  # Remplacez par l'ID de votre groupe Telegram
bot = telebot.TeleBot(TOKEN)

# Tentative de connexion à la base de données SQLite avec gestion des erreurs de verrouillage
max_retries = 5
for attempt in range(max_retries):
    try:
        conn = sqlite3.connect('users.db', check_same_thread=False)
        cursor = conn.cursor()
        logger.info("Connexion à la base de données SQLite réussie.")
        break
    except sqlite3.OperationalError as e:
        if 'locked' in str(e).lower():
            logger.warning(f"La base de données est verrouillée, nouvelle tentative dans 1 seconde (Tentative {attempt + 1}/{max_retries}).")
            time.sleep(1)
        else:
            logger.error(f"Erreur de connexion à la base de données: {e}")
            raise
else:
    logger.error("Impossible de se connecter à la base de données après plusieurs tentatives.")
    raise sqlite3.OperationalError("Base de données verrouillée après plusieurs tentatives.")

# Création des tables 'users' et 'balances' si elles n'existent pas déjà
logger.info("Vérification de la présence des tables nécessaires dans la base de données.")
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    address TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS balances (
    user_id INTEGER PRIMARY KEY,
    address TEXT,
    balance REAL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
''')

conn.commit()
logger.info("Tables 'users' et 'balances' vérifiées/créées avec succès.")

# Commande /start
@bot.message_handler(commands=['start'])
def send_welcome(message: Message):
    logger.info(f"Commande /start reçue de l'utilisateur {message.chat.id}")
    response = (
        "/register <adresse_wallet> - Enregistre votre adresse pour surveiller les blocs trouvés\n"
        "/unregister - Supprime votre adresse enregistrée\n"
        "/balance - Affiche votre solde actuel pour l'adresse enregistrée\n"
        "/setname <nom> - Définit ou met à jour votre nom\n"
        "/start - Affiche cette liste de commandes"
    )
    bot.reply_to(message, response)

# Commande /register
@bot.message_handler(commands=['register'])
def register_wallet(message: Message):
    if message.chat.type in ['group', 'supergroup', 'private'] and (message.chat.type == 'private' or message.chat.id == GROUP_CHAT_ID):
        try:
            wallet_address = message.text.split()[1]
            user_id = message.from_user.id
            username = message.from_user.username if message.from_user.username else "Inconnu"

            # Vérifier si l'adresse est déjà enregistrée
            cursor.execute('SELECT * FROM users WHERE address = ?', (wallet_address,))
            if cursor.fetchone():
                bot.reply_to(message, "Cette adresse est déjà enregistrée dans la base de données.")
                logger.warning(f"Adresse {wallet_address} déjà enregistrée pour l'utilisateur {user_id}.")
                return

            logger.info(f"Enregistrement de l'adresse {wallet_address} pour l'utilisateur {user_id}")
            # Utilisation de sedractl pour obtenir le solde initial
            command = ['./sedractl', 'GetBalanceByAddress', wallet_address]
            balance_output = subprocess.check_output(command, text=True).strip()
            balance_json = json.loads(balance_output)
            balance_raw = balance_json["getBalanceByAddressResponse"]["balance"]
            balance = int(balance_raw) / 100000000.0  # Ajustement de l'échelle correcte
            # Insertion de l'utilisateur dans la table 'users' et de son solde dans la table 'balances'
            cursor.execute('INSERT OR REPLACE INTO users (user_id, username, address) VALUES (?, ?, ?)', (user_id, username, wallet_address))
            cursor.execute('INSERT OR REPLACE INTO balances (user_id, address, balance) VALUES (?, ?, ?)', (user_id, wallet_address, balance))
            conn.commit()
            bot.reply_to(message, f"Adresse enregistrée: {wallet_address} avec un solde de: {balance:,.8f} SDR.")
            logger.info(f"Utilisateur {user_id} enregistré avec un solde de {balance:,.8f} SDR.")
        except IndexError:
            bot.reply_to(message, "Veuillez fournir une adresse de wallet valide. Exemple: /register <adresse_wallet>")
            logger.warning(f"Utilisateur {message.chat.id} n'a pas fourni d'adresse valide pour /register.")
        except subprocess.CalledProcessError:
            bot.reply_to(message, "Erreur lors de la récupération du solde initial. Veuillez réessayer plus tard.")
            logger.error(f"Erreur lors de l'exécution de la commande sedractl pour l'utilisateur {message.chat.id}.")
        except json.JSONDecodeError:
            bot.reply_to(message, "Erreur lors de la lecture de la réponse du solde. Veuillez réessayer plus tard.")
            logger.error(f"Erreur de parsing JSON pour l'utilisateur {message.chat.id} lors de /register.")

# Commande /unregister
@bot.message_handler(commands=['unregister'])
def unregister_wallet(message: Message):
    if message.chat.type in ['group', 'supergroup', 'private'] and (message.chat.type == 'private' or message.chat.id == GROUP_CHAT_ID):
        user_id = message.from_user.id
        logger.info(f"Suppression de l'utilisateur {user_id} de la base de données.")
        cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
        cursor.execute('DELETE FROM balances WHERE user_id = ?', (user_id,))
        conn.commit()
        bot.reply_to(message, "Votre adresse a été supprimée avec succès.")
        logger.info(f"Utilisateur {user_id} supprimé avec succès.")

# Commande /balance
@bot.message_handler(commands=['balance'])
def show_balance(message: Message):
    if message.chat.type in ['group', 'supergroup', 'private'] and (message.chat.type == 'private' or message.chat.id == GROUP_CHAT_ID):
        user_id = message.from_user.id
        logger.info(f"Récupération du solde pour l'utilisateur {user_id}")
        cursor.execute('SELECT address FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        if result:
            wallet_address = result[0]
            try:
                # Utilisation de sedractl pour mettre à jour le solde
                command = ['./sedractl', 'GetBalanceByAddress', wallet_address]
                balance_output = subprocess.check_output(command, text=True).strip()
                balance_json = json.loads(balance_output)
                balance_raw = balance_json["getBalanceByAddressResponse"]["balance"]
                balance = int(balance_raw) / 100000000.0  # Ajustement de l'échelle correcte
                # Mise à jour de la base de données avec le nouveau solde
                cursor.execute('UPDATE balances SET balance = ? WHERE user_id = ?', (balance, user_id))
                conn.commit()
                bot.reply_to(message, f"Votre solde actuel est de: {balance:,.8f} SDR.")
                logger.info(f"Solde mis à jour pour l'utilisateur {user_id}: {balance:,.8f} SDR.")
            except subprocess.CalledProcessError:
                bot.reply_to(message, "Erreur lors de la récupération du solde. Veuillez réessayer plus tard.")
                logger.error(f"Erreur lors de l'exécution de la commande sedractl pour l'utilisateur {user_id} lors de /balance.")
            except json.JSONDecodeError:
                bot.reply_to(message, "Erreur lors de la lecture de la réponse du solde. Veuillez réessayer plus tard.")
                logger.error(f"Erreur de parsing JSON pour l'utilisateur {user_id} lors de /balance.")
        else:
            bot.reply_to(message, "Aucune adresse enregistrée. Utilisez /register pour enregistrer une adresse.")
            logger.warning(f"Aucune adresse trouvée pour l'utilisateur {user_id} lors de /balance.")

# Commande /setname
@bot.message_handler(commands=['setname'])
def set_name(message: Message):
    if message.chat.type in ['group', 'supergroup', 'private'] and (message.chat.type == 'private' or message.chat.id == GROUP_CHAT_ID):
        try:
            name = message.text.split()[1]
            user_id = message.from_user.id
            cursor.execute('UPDATE users SET username = ? WHERE user_id = ?', (name, user_id))
            conn.commit()
            bot.reply_to(message, f"Votre nom a été mis à jour: {name}")
            logger.info(f"Nom mis à jour pour l'utilisateur {user_id}: {name}")
        except IndexError:
            bot.reply_to(message, "Veuillez fournir un nom valide. Exemple: /setname <nom>")
            logger.warning(f"Utilisateur {user_id} n'a pas fourni de nom valide pour /setname.")

# Gestion des messages dans les groupes
@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_group_messages(message: Message):
    if message.chat.type in ['group', 'supergroup'] and message.chat.id == GROUP_CHAT_ID:
        logger.info(f"Message reçu dans le groupe {GROUP_CHAT_ID} de l'utilisateur {message.chat.id}: {message.text}")
        if message.text.startswith('/register'):
            register_wallet(message)
        elif message.text.startswith('/unregister'):
            unregister_wallet(message)
        elif message.text.startswith('/balance'):
            show_balance(message)
        elif message.text.startswith('/setname'):
            set_name(message)
        elif message.text.startswith('/start'):
            send_welcome(message)

# Démarrer le bot
logger.info("Démarrage du bot Telegram.")
bot.polling()

