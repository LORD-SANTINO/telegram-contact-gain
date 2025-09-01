import asyncio
import os
import random
import time
import vobject
import json
from telethon import TelegramClient, events
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.tl.types import InputPhoneContact, InputUser
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.errors import PeerFloodError, UserPrivacyRestrictedError, FloodWaitError

# Your credentials
session_name = 'userbot_session'  # Session file name
contacts_file = 'stored_contacts.json'  # Persistent storage
failed_file = 'failed_contacts.json'    # Failed contacts storage
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')
phone = os.getenv('PHONE')

# Check if credentials are set
if not all([api_id, api_hash, phone]):
    raise ValueError("Please set API_ID, API_HASH, and PHONE environment variables.")

# Create the client
client = TelegramClient(session_name, api_id, api_hash)

# State management
user_states = {}
stored_users = []
owner_id = None


# === Storage Helpers ===
def load_stored_users():
    global stored_users
    if os.path.exists(contacts_file):
        with open(contacts_file, 'r') as f:
            stored_users = json.load(f)
        print(f"Loaded {len(stored_users)} stored users.")
    else:
        print("No stored contacts file found.")


def save_stored_users():
    with open(contacts_file, 'w') as f:
        json.dump(stored_users, f)
    print("Stored users saved.")


def save_failed_contacts(failed_contacts):
    with open(failed_file, 'w') as f:
        json.dump(failed_contacts, f)
    print("Failed contacts saved.")


# === Bot Commands ===
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    global owner_id
    if owner_id is None:
        me = await client.get_me()
        owner_id = me.id

    await event.reply(
        "Welcome! To add members to a channel, please use the /addmembers command.\n"
        "Admin can use /upload_vcf to upload contacts."
    )


@client.on(events.NewMessage(pattern='/addmembers'))
async def addmembers_handler(event):
    chat_id = event.chat_id
    user_states[chat_id] = {'step': 'channel_link'}
    await event.reply("Please drop your Telegram channel link (e.g., https://t.me/channelname or @channelname).")


@client.on(events.NewMessage)
async def message_handler(event):
    if event.raw_text.startswith("/"):
        # Ignore commands here except handled separately
        return

    chat_id = event.chat_id
    if chat_id not in user_states:
        return

    state = user_states[chat_id]
    message = event.message

    if state['step'] == 'channel_link':
        channel_link = message.text.strip()
        try:
            channel = await client.get_entity(channel_link)
            state['channel'] = channel
            state['step'] = 'num_members'
            await event.reply("How many members do you want to add?")
        except Exception as e:
            await event.reply(f"Invalid channel link: {str(e)}. Try again.")

    elif state['step'] == 'num_members':
        try:
            num_members = int(message.text.strip())
            if num_members <= 0:
                raise ValueError
            state['num_members'] = num_members
            if len(stored_users) == 0:
                await event.reply("No contacts stored yet. Contact the admin to upload a VCF.")
                del user_states[chat_id]
                return
            await add_members_from_stored(event, state)
            del user_states[chat_id]
        except ValueError:
            await event.reply("Please enter a valid number.")


async def add_members_from_stored(event, state):
    channel = state['channel']
    num_members = state['num_members']
    selected_users = random.sample(stored_users, min(num_members, len(stored_users)))

    added_count = 0
    for user_dict in selected_users:
        try:
            user = InputUser(user_id=user_dict['id'], access_hash=user_dict['access_hash'])
            await client(InviteToChannelRequest(channel=channel, users=[user]))
            added_count += 1
            await event.reply(f"Added {user_dict['first_name']} (ID: {user_dict['id']}) to the channel.")
            time.sleep(60)  # delay to avoid flood
        except PeerFloodError:
            await event.reply("Flood error: Too many requests. Stopping.")
            break
        except UserPrivacyRestrictedError:
            await event.reply(f"Cannot add {user_dict['first_name']}: Privacy restricted.")
        except Exception as e:
            await event.reply(f"Error adding {user_dict['first_name']}: {str(e)}")

    await event.reply(f"Process complete. Added {added_count} members.")


@client.on(events.NewMessage(pattern='/upload_vcf'))
async def upload_vcf_handler(event):
    global owner_id
    if owner_id is None:
        me = await client.get_me()
        owner_id = me.id

    if event.sender_id != owner_id:
        await event.reply("You are not authorized to upload VCF.")
        return
    await event.reply("Upload your VCF file now.")


@client.on(events.NewMessage)
async def receive_vcf(event):
    if event.sender_id != owner_id or not event.document:
        return
    if (event.document.mime_type == 'text/vcard' or
            (event.document.attributes and event.document.attributes[0].file_name.endswith('.vcf'))):
        vcf_path = await event.message.download_media(file='temp.vcf')
        await process_and_store_vcf(event, vcf_path)
    else:
        await event.reply("Please upload a valid .vcf file.")


# === Safe Import Logic ===
async def process_and_store_vcf(event, vcf_path):
    global stored_users

    contacts = []
    with open(vcf_path, 'r', encoding='utf-8') as f:
        for vcard in vobject.readComponents(f):
            name = vcard.fn.value if hasattr(vcard, 'fn') else 'Unknown'
            if hasattr(vcard, 'tel'):
                for tel in vcard.tel_list:
                    phone_num = tel.value.strip().replace(' ', '').replace('-', '')
                    if phone_num.startswith('+'):
                        contacts.append({'phone': phone_num, 'name': name})

    if not contacts:
        await event.reply("No valid contacts found in VCF.")
        os.remove(vcf_path)
        return

    new_stored_users = []
    failed_contacts = []
    batch_size = 30
    pause_between = 10

    for i in range(0, len(contacts), batch_size):
        batch = contacts[i:i + batch_size]
        phone_contacts = [
            InputPhoneContact(
                client_id=random.randint(0, 999999),
                phone=c['phone'],
                first_name=c['name'],
                last_name=''
            ) for c in batch
        ]

        try:
            result = await client(ImportContactsRequest(contacts=phone_contacts))
            for user in result.users:
                new_stored_users.append({
                    'id': user.id,
                    'access_hash': user.access_hash,
                    'first_name': user.first_name or 'Unknown',
                    'phone': user.phone
                })

            save_stored_users()
            await event.reply(f"✅ Imported batch {i // batch_size + 1}")
            await asyncio.sleep(pause_between)

        except FloodWaitError as e:
            await event.reply(f"⏳ FloodWait: Sleeping {e.seconds} seconds...")
            await asyncio.sleep(e.seconds)
            continue
        except Exception as e:
            failed_contacts.extend(batch)
            save_failed_contacts(failed_contacts)
            print(f"❌ Error importing batch {i // batch_size + 1}: {str(e)}")

    if new_stored_users:
        stored_users.extend(new_stored_users)
        save_stored_users()
        await event.reply(f"Stored {len(stored_users)} users from VCF.")
    else:
        await event.reply("No users imported from VCF.")

    os.remove(vcf_path)


async def main():
    await client.start(phone=phone)
    load_stored_users()
    print("Userbot is running. As admin, message /upload_vcf to upload VCF. Users can use /addmembers.")
    client.add_event_handler(receive_vcf, events.NewMessage())
    await client.run_until_disconnected()


if __name__ == '__main__':
    client.loop.run_until_complete(main())
        
