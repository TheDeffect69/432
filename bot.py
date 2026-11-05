import argparse
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import requests
import vk_api
from dotenv import load_dotenv
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll
from vk_api.exceptions import ApiError, ApiHttpError
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.upload import VkUpload
from vk_api.utils import get_random_id

from steganography import hide_text, read_text

BASE_DIR = Path(__file__).resolve().parent
MODE = "extended"
PHOTO = BASE_DIR / "images/welcome.png"
vk = None
upload = None
states = {}


class TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (5, 15))
        return super().request(method, url, **kwargs)


def status(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


keyboard = VkKeyboard(one_time=False)
keyboard.add_button("Помощь", color=VkKeyboardColor.PRIMARY)
keyboard.add_button("Отправить фото", color=VkKeyboardColor.POSITIVE)
keyboard.add_line()
keyboard.add_button("Скрыть текст", color=VkKeyboardColor.PRIMARY)
keyboard.add_button("Прочитать текст", color=VkKeyboardColor.POSITIVE)
keyboard = keyboard.get_keyboard()

help_text = (
    "/start — меню\n/help — справка\n/photo — прислать фото\n"
    "/hide — спрятать текст в PNG\n/read — прочитать скрытый текст\n\n"
    "Обычные сообщения я повторяю."
)


def send(peer_id, text, attachment=None):
    data = {"peer_id": peer_id, "message": text, "random_id": get_random_id()}
    if MODE == "extended":
        data["keyboard"] = keyboard
    if attachment:
        data["attachment"] = attachment
    vk.messages.send(**data)


def send_photo(peer_id):
    photo = upload.photo_messages(photos=str(PHOTO), peer_id=peer_id)[0]
    send(peer_id, "Держи картинку! 📸", f"photo{photo['owner_id']}_{photo['id']}")


def download(url):
    response = requests.get(url, timeout=(5, 30))
    response.raise_for_status()
    return response.content


def get_file(message):
    for item in message.get("attachments", []):
        attachment = item.get(item.get("type"), {})
        if item.get("type") == "doc" and attachment.get("url"):
            return download(attachment["url"])
        if item.get("type") == "photo":
            sizes = attachment.get("sizes", [])
            if sizes:
                url = max(sizes, key=lambda size: size["width"] * size["height"])["url"]
                return download(url)


def handle_event(event):
    if event.type != VkBotEventType.MESSAGE_NEW:
        return
    message = event.message
    if message.get("out"):
        return

    user_id = message["from_id"]
    peer_id = message["peer_id"]
    text = message.get("text", "")
    state = states.get(user_id, {})

    if state.get("action") == "hide_text":
        states[user_id] = {"action": "hide_image", "text": text}
        send(peer_id, "Теперь отправь картинку. Я верну её PNG-файлом со скрытым текстом.")
        return

    if state.get("action") == "hide_image":
        try:
            with TemporaryDirectory() as folder:
                path = Path(folder) / "secret.png"
                hide_text(get_file(message), state["text"], path)
                result = upload.document_message(str(path), title="secret.png", peer_id=peer_id)
            doc = result.get("doc", result)
            attachment = f"doc{doc['owner_id']}_{doc['id']}"
            if doc.get("access_key"):
                attachment += f"_{doc['access_key']}"
            send(peer_id, "Готово! Передай этот PNG-файл получателю. 📩", attachment)
            states[user_id] = {}
        except ApiError as error:
            states[user_id] = {}
            if error.code == 15:
                send(peer_id, "Для отправки PNG создай ключ сообщества с правом «Документы» и замени GROUP_TOKEN в .env.")
            else:
                raise
        except (ValueError, TypeError, requests.RequestException):
            send(peer_id, "Не получилось обработать файл. Отправь картинку и попробуй ещё раз.")
        return

    if state.get("action") == "read_image":
        try:
            send(peer_id, read_text(get_file(message)))
        except (ValueError, TypeError, requests.RequestException, StopIteration):
            send(peer_id, "Не удалось прочитать сообщение. Отправь PNG-файл, созданный ботом.")
        states[user_id] = {}
        return

    command = text.strip().casefold()
    if MODE == "echo":
        if text:
            send(peer_id, text)
    elif command in {"/start", "start", "старт", "меню"}:
        send(peer_id, "Привет! Я учебный VK-бот 🤖\n\n" + help_text)
    elif command in {"/help", "help", "помощь", "справка"}:
        send(peer_id, help_text)
    elif command in {"/photo", "photo", "фото", "отправить фото"}:
        send_photo(peer_id)
    elif command in {"/hide", "скрыть текст", "спрятать текст"}:
        states[user_id] = {"action": "hide_text"}
        send(peer_id, "Напиши текст, который нужно спрятать.")
    elif command in {"/read", "прочитать текст"}:
        states[user_id] = {"action": "read_image"}
        send(peer_id, "Отправь PNG-файл с секретным сообщением как документ.")
    elif text:
        send(peer_id, text)



def api_error_message(error):
    hints = {
        5: "Проверь GROUP_TOKEN в .env: ключ недействителен.",
        15: "Проверь права ключа сообщества и включение Bots Long Poll в VK.",
        100: "Проверь GROUP_ID и настройки Bots Long Poll в VK.",
    }
    return f"Ошибка VK {error.code}. " + hints.get(error.code, "Проверь настройки сообщества VK.")


def run_bot(session, group_id, check_only=False):
    longpoll = None
    delay = 1
    disconnected = False
    while True:
        try:
            if longpoll is None:
                status("Подключаюсь к VK...")
                longpoll = VkBotLongPoll(session, group_id)
                if check_only:
                    status("Подключение к VK проверено. Настройки приняты; сообщения не отправлялись.")
                    return 0
                status("Бот запущен. Напиши /start в сообщения сообщества. Для остановки нажми Ctrl+C.")
            events = longpoll.check()
        except ApiError as error:
            status(api_error_message(error))
            return 1
        except (requests.RequestException, ApiHttpError, ValueError) as error:
            status(f"Нет ответа от VK ({type(error).__name__})." +
                   ("" if check_only else f" Повтор подключения через {delay} с."))
            if check_only:
                return 1
            disconnected = True
            time.sleep(delay)
            delay = min(delay * 2, 15)
            continue
        if disconnected:
            status("Связь с VK восстановлена. Бот ждёт сообщения.")
        disconnected = False
        delay = 1
        for event in events:
            try:
                handle_event(event)
            except ApiError as error:
                status(api_error_message(error))
            except (requests.RequestException, ApiHttpError, OSError, ValueError, TypeError, StopIteration) as error:
                status(f"Не удалось обработать сообщение ({type(error).__name__}). Бот продолжает работу.")


def main(argv=None):
    global MODE, PHOTO, vk, upload
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Учебный VK-бот")
    parser.add_argument("--check-connection", action="store_true", help="Проверить подключение без обработки сообщений")
    parser.add_argument("--mode", choices=("echo", "extended"))
    args = parser.parse_args(argv)
    status("Запускаю бота...")
    load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")
    try:
        token = os.getenv("GROUP_TOKEN", "").strip()
        if not token:
            raise ValueError("Заполни GROUP_TOKEN в файле .env.")
        try:
            group_id = int(os.getenv("GROUP_ID", ""))
            if group_id <= 0:
                raise ValueError
        except ValueError:
            raise ValueError("В GROUP_ID нужно положительное число — ID сообщества.") from None
        MODE = args.mode or os.getenv("BOT_MODE", "extended").strip().lower()
        if MODE not in {"echo", "extended"}:
            raise ValueError("BOT_MODE должен быть echo или extended.")
        PHOTO = BASE_DIR / os.getenv("PHOTO_PATH", "images/welcome.png")
        if MODE == "extended" and not PHOTO.is_file():
            raise ValueError("Не найдена картинка. Проверь PHOTO_PATH в .env.")
    except ValueError as error:
        status(str(error))
        return 1
    with TimeoutSession() as http:
        session = vk_api.VkApi(token=token, api_version=os.getenv("VK_API_VERSION", "5.199"), session=http)
        vk = session.get_api()
        upload = VkUpload(session)
        upload.http.close()
        upload.http = http
        try:
            return run_bot(session, group_id, check_only=args.check_connection)
        except KeyboardInterrupt:
            status("Бот остановлен.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
