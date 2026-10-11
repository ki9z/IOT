"""Test connection to MongoDB Atlas using .env; no password is printed."""
import os
import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError

load_dotenv()
uri = os.getenv("MONGO_URI", "").strip()
if not uri or "<" in uri or ">" in uri:
    raise SystemExit("MONGO_URI trong .env chua duoc dien chinh xac.")
client = MongoClient(uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=12000)
try:
    client.admin.command("ping")
    print("[OK] MongoDB Atlas da ket noi thanh cong!")
    db = client[os.getenv("MONGO_DB", "iot_lab2")]
    print("Database:", db.name)
    print("Collections:", db.list_collection_names())
except PyMongoError as exc:
    print("[ERROR]", type(exc).__name__)
    print("Kiem tra URI, database username/password, Network Access va ket noi mang.")
finally:
    client.close()
