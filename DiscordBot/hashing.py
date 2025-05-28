from enum import Enum
import imagehash
from datetime import datetime
import os
import json
import io
from PIL import Image
import discord

SAVE_NAME = "hashes.json"
SAVE_DIR = "./hashdb"
THRESHOLD = 10

async def get_phash_from_discord_attachment(attachment: discord.Attachment) -> imagehash.ImageHash:
    file_bytes = await attachment.read()
    image = Image.open(io.BytesIO(file_bytes))
    phash = imagehash.phash(image)
    return phash
    
class AIGeneratedOption(Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"

class HashRecord:
    def __init__(self, phash: imagehash.ImageHash | str, date_created: datetime = datetime.now(), ai_generated: AIGeneratedOption = AIGeneratedOption.UNKNOWN):
        self.phash = imagehash.hex_to_hash(phash) if isinstance(phash, str) else phash
        self.date_created = date_created
        self.ai_generated = ai_generated

class HashDB:
    def __init__(self):
        self.hashes = []        
    
    def add_record(self, record: HashRecord):
        self.hashes.append(record)
        self.save()
    
    def get_record(self, phash: imagehash.ImageHash) -> HashRecord | None:
        for record in self.hashes:
            if record.phash == phash:
                return record
        return None

    def remove_record(self, phash: imagehash.ImageHash):
        self.hashes = [record for record in self.hashes if record.phash != phash]
        self.save()

    def check_matching_hash(self, phash: imagehash.ImageHash) -> HashRecord | None:
        for record in self.hashes:
            if record.phash - phash < THRESHOLD:
                return record
        return None

    def save(self):        
        os.makedirs(SAVE_DIR, exist_ok=True)
        with open(os.path.join(SAVE_DIR, SAVE_NAME), "w") as f:
            json.dump([{
                'phash': str(record.phash),
                'date_created': record.date_created.isoformat(),
                'ai_generated': record.ai_generated.value
            } for record in self.hashes], f)

    @classmethod
    def load(cls):
        db = cls()
        if not os.path.exists(os.path.join(SAVE_DIR, SAVE_NAME)):
            return db
        
        with open(os.path.join(SAVE_DIR, SAVE_NAME), "r") as f:
            data = json.load(f)
            for record_data in data:
                db.hashes.append(HashRecord(
                    phash=record_data['phash'],
                    date_created=datetime.fromisoformat(record_data['date_created']),
                    ai_generated=AIGeneratedOption(record_data['ai_generated'])
                ))
        return db
