import os
from pathlib import Path
from tempfile import TemporaryDirectory

import requests
import vk_api
from dotenv import load_dotenv
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll
from vk_api.exceptions import ApiError
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.upload import VkUpload
from vk_api.utils import get_random_id

from steganography import hide_text, read_text

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")
TOKEN = os.getenv("GROUP_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
PHOTO = BASE_DIR / os.getenv("PHOTO_PATH", "images/welcome.png")
MODE = os.getenv("BOT_MODE", "extended")

session = vk_api.VkApi(token=TOKEN, api_version=os.getenv("VK_API_VERSION", "5.199"))
vk = session.get_api()
upload = VkUpload(session)
longpoll = VkBotLongPoll(session, GROUP_ID)
states = {}

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


def get_file(message):
    for item in message.get("attachments", []):
        attachment = item.get(item.get("type"), {})
        if item.get("type") == "doc" and attachment.get("url"):
            return requests.get(attachment["url"]).content
        if item.get("type") == "photo":
            sizes = attachment.get("sizes", [])
            if sizes:
                url = max(sizes, key=lambda size: size["width"] * size["height"])["url"]
                return requests.get(url).content


for event in longpoll.listen():
    if event.type != VkBotEventType.MESSAGE_NEW:
        continue
    message = event.message
    if message.get("out"):
        continue

    user_id = message["from_id"]
    peer_id = message["peer_id"]
    text = message.get("text", "")
    state = states.get(user_id, {})

    if state.get("action") == "hide_text":
        states[user_id] = {"action": "hide_image", "text": text}
        send(peer_id, "Теперь отправь картинку. Я верну её PNG-файлом со скрытым текстом.")
        continue

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
        continue

    if state.get("action") == "read_image":
        try:
            send(peer_id, read_text(get_file(message)))
        except (ValueError, TypeError, requests.RequestException, StopIteration):
            send(peer_id, "Не удалось прочитать сообщение. Отправь PNG-файл, созданный ботом.")
        states[user_id] = {}
        continue

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
