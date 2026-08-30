import requests

def test_telegram_connection(bot_token, chat_id):
    """Sends a test Telegram notification."""
    logs = []
    if not bot_token or not chat_id:
        return False, "Bot Token или Chat ID не указаны.", logs
    try:
        logs.append("[1/2] Проверка Bot Token через Telegram Bot API...")
        r_me = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=10)
        if r_me.status_code != 200:
            err = f"Ошибка Telegram Bot Token [{r_me.status_code}]: {r_me.text}"
            logs.append(err)
            return False, err, logs

        bot_name = r_me.json().get('result', {}).get('first_name', 'Bot')
        logs.append(f"✔ Бот найден: @{r_me.json().get('result', {}).get('username')} ({bot_name})")

        logs.append(f"[2/2] Отправка тестового сообщения в Chat ID {chat_id}...")
        text = "🔔 <b>Asterisk Logic Core PBX</b>\nТестовое уведомление: соединение с Telegram успешно настроено!"
        first_chat = chat_id.split(',')[0].strip()
        r_send = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": first_chat, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
        if r_send.status_code == 200:
            logs.append("✔ Сообщение успешно доставлено в Telegram!")
            return True, "Тест Telegram успешно пройден!", logs
        else:
            err = f"Ошибка доставки сообщения [{r_send.status_code}]: {r_send.text}"
            logs.append(err)
            return False, err, logs
    except Exception as e:
        err = f"Исключение при проверке Telegram: {str(e)}"
        logs.append(err)
        return False, err, logs
