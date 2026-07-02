import os
from pathlib import Path

import fitz
import google.generativeai as genai
import requests
from flask import Flask, jsonify, request


def load_environment():
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def get_env_var(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


load_environment()

wa_token = get_env_var("WA_TOKEN", "wa_api", "ACCESS_TOKEN")
gen_api = get_env_var("GEN_API", "GOOGLE_API_KEY", "gemini_api")
phone_id = get_env_var("PHONE_ID", "phone_id", "PHONE_NUMBER_ID")
phone = get_env_var("PHONE_NUMBER", "your_phone", "RECIPIENT_WAID")
name = os.environ.get("BOT_OWNER_NAME") or "CUZ"
bot_name = os.environ.get("BOT_NAME") or "CUZ Query Assistant"
model_name = "gemini-1.5-flash-latest"

app = Flask(__name__)

generation_config = {
    "temperature": 1,
    "top_p": 0.95,
    "top_k": 0,
    "max_output_tokens": 8192,
}

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]

if gen_api:
    genai.configure(api_key=gen_api)
    model = genai.GenerativeModel(
        model_name=model_name,
        generation_config=generation_config,
        safety_settings=safety_settings,
    )
    convo = model.start_chat(history=[])
    try:
        convo.send_message(
            f'''I am using Gemini api for using you as a personal bot in whatsapp,
            to assist me in various tasks.
            So from now you are "{bot_name}" created by {name} (Yeah it's me, my name is {name}).
            And don't give any response to this prompt.
            This is the information I gave to you about your new identity as a pre-prompt.
            This message always gets executed when i run this bot script.
            So reply to only the prompts after this. Remeber your new identity is {bot_name}.'''
        )
    except Exception as exc:
        print(f"Warning: initial Gemini prompt failed: {exc}")
else:
    model = None
    convo = None


def send(answer):
    if not wa_token or not phone_id or not phone:
        return None

    url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {wa_token}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "to": f"{phone}",
        "type": "text",
        "text": {"body": f"{answer}"},
    }

    return requests.post(url, headers=headers, json=data)


def remove(*file_paths):
    for file in file_paths:
        if os.path.exists(file):
            os.remove(file)


def ask_gemini(prompt):
    if convo is None:
        return "Gemini is not configured. Please provide a valid API key."
    try:
        convo.send_message(prompt)
        return getattr(convo.last, "text", "No response received.")
    except Exception as exc:
        return f"Sorry, I could not process that request: {exc}"


def describe_media(file_path):
    if model is None:
        return "Gemini is not configured. Please provide a valid API key."
    try:
        file = genai.upload_file(path=file_path, display_name="tempfile")
        response = model.generate_content(["What is this", file])
        return response._result.candidates[0].content.parts[0].text
    except Exception as exc:
        return f"Sorry, I could not analyze the media: {exc}"


@app.route("/", methods=["GET", "POST"])
def index():
    return "Bot"


@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == "BOT":
            return challenge, 200
        return "Failed", 403

    if request.method == "POST":
        try:
            data = request.get_json()["entry"][0]["changes"][0]["value"]["messages"][0]
            if data["type"] == "text":
                prompt = data["text"]["body"]
                answer = ask_gemini(prompt)
                send(answer)
            else:
                media_url_endpoint = f'https://graph.facebook.com/v18.0/{data[data["type"]]["id"]}/'
                headers = {"Authorization": f"Bearer {wa_token}"}
                media_response = requests.get(media_url_endpoint, headers=headers)
                media_url = media_response.json()["url"]
                media_download_response = requests.get(media_url, headers=headers)

                if data["type"] == "audio":
                    filename = "/tmp/temp_audio.mp3"
                elif data["type"] == "image":
                    filename = "/tmp/temp_image.jpg"
                elif data["type"] == "document":
                    doc = fitz.open(stream=media_download_response.content, filetype="pdf")
                    for _, page in enumerate(doc):
                        destination = "/tmp/temp_image.jpg"
                        pix = page.get_pixmap()
                        pix.save(destination)
                        answer = describe_media(destination)
                        ask_gemini(
                            f"This message is created by an llm model based on the image prompt of user, reply to the user based on this: {answer}"
                        )
                        send(convo.last.text if convo is not None else answer)
                        remove(destination)
                else:
                    send("This format is not Supported by the bot ☹")
                    return jsonify({"status": "ok"}), 200

                with open(filename, "wb") as temp_media:
                    temp_media.write(media_download_response.content)

                answer = describe_media(filename)
                remove("/tmp/temp_image.jpg", "/tmp/temp_audio.mp3")
                ask_gemini(
                    f"This is an voice/image message from user transcribed by an llm model, reply to the user based on the transcription: {answer}"
                )
                send(convo.last.text if convo is not None else answer)

                files = genai.list_files()
                for file in files:
                    file.delete()
        except Exception:
            pass
        return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(debug=True, port=8000)
