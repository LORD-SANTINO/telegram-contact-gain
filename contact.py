import asyncio
import os
import random
import time
import vobject
import json
from telethon import TelegramClient, events
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.tl.types import InputPhoneContact, InputPeerChannel, InputUser
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.errors import PeerFloodError, UserPrivacyRestrictedError

# Your credentials
api_id = YOUR_API_ID  # Integer
api_hash = 'YOUR_API_HASH'  # String
phone = '+YOUR_PHONE_NUMBER'  # e.g., '+1234567890'
session_name = 'userbot_session'  # Session file name
contacts_file = 'stored_contacts.json'  # Persistent storage

# Create the client
client = TelegramClient(session_name, api_id, api_hash)

# State management (simple dict for user chats)
user_states = {}  # {chat_id: {'step': 'start', 'channel': None, 'num_members': None}}

# Stored users (list of dicts: {'id': int, 'access_hash': int, 'first_name': str})
stored_users = []

# Owner ID (will be set on start)
owner_id = None

# Load stored users from JSON if exists
def load_stored_users():
    global stored_users
    if os.path.exists(contacts_file):
        with open(contacts_file, 'r') as f:
            stored_users = json.load(f)
        print(f"Loaded {len(stored_users)} stored users.")
    else:
        print("No stored contacts file found.")

# Save stored users to JSON
def save_stored_users():
    with open(contacts_file, 'w') as f:
        json.dump(stored_users, f)
    print("Stored users saved.")

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    global owner_id
    if owner_id is None:
        me = await client.get_me()
        owner_id = me.id

    chat_id = event.chat_id
    user_states[chat_id] = {'step': 'channel_link'}
    await event.reply("Please drop your Telegram channel link (e.g., https://t.me/channelname or @channelname).")

@client.on(events.NewMessage)
async def message_handler(event):
    chat_id = event.chat_id
    if chat_id not in user_states:
        return

    state = user_states[chat_id]
    message = event.message

    if state['step'] == 'channel_link':
        channel_link = message.text.strip()
        try:
            channel = await client.get_entity(channel_link)
            if not isinstance(channel, InputPeerChannel):
                raise ValueError("Not a channel.")
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
            del user_states[chat_id]  # Reset state
        except ValueError:
            await event.reply("Please enter a valid number.")

async def add_members_from_stored(event, state):
    channel = state['channel']
    num_members = state['num_members']

    # Select random users to add (to vary selections)
    selected_users = random.sample(stored_users, min(num_members, len(stored_users)))

    added_count = 0
    for user_dict in selected_users:
        try:
            user = InputUser(user_id=user_dict['id'], access_hash=user_dict['access_hash'])
            await client(InviteToChannelRequest(channel=channel, users=[user]))
            added_count += 1
            await event.reply(f"Added {user_dict['first_name']} (ID: {user_dict['id']}) to the channel.")
            time.sleep(60)  # Sleep to avoid flood (adjust as needed)
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
    if event.sender_id != owner_id:
        await event.reply("You are not authorized to upload VCF.")
        return
    await event.reply("Upload your VCF file now.")

@client.on(events.NewMessage)
async def receive_vcf(event):
    if event.sender_id != owner_id or not event.document:
        return
    # Check if it's a VCF file
    if event.document.mime_type == 'text/vcard' or event.document.attributes[0].file_name.endswith('.vcf'):
        vcf_path = await event.message.download_media(file='temp.vcf')
        await process_and_store_vcf(event, vcf_path)
    else:
        await event.reply("Please upload a valid .vcf file.")

async def process_and_store_vcf(event, vcf_path):
    global stored_users

    # Parse VCF
    contacts = []
    with open(vcf_path, 'r', encoding='utf-8') as f:
        for vcard in vobject.readComponents(f):
            name = vcard.fn.value if hasattr(vcard, 'fn') else 'Unknown'
            if hasattr(vcard, 'tel'):
                for tel in vcard.tel_list:
                    phone_num = tel.value.strip().replace(' ', '').replace('-', '')
                    if phone_num.startswith('+'):  # Ensure international format
                        contacts.append({'phone': phone_num, 'name': name})

    if not contacts:
        await event.reply("No valid contacts found in VCF.")
        os.remove(vcf_path)
        return

    # Import contacts and collect users
    new_stored_users = []
    for contact in contacts:
        try:
            result = await client(ImportContactsRequest(
                contacts=[InputPhoneContact(client_id=random.randint(0, 999), phone=contact['phone'], first_name=contact['name'], last_name='')]
            ))
            for user in result.users:
                new_stored_users.append({
                    'id': user.id,
                    'access_hash': user.access_hash,
                    'first_name': user.first_name or 'Unknown'
                })
        except Exception as e:
            print(f"Import error for {contact['phone']}: {str(e)}")

    if new_stored_users:
        stored_users = new_stored_users  # Replace or append? Here, replace for simplicity.
        save_stored_users()
        await event.reply(f"Stored {len(stored_users)} users from VCF.")
    else:
        await event.reply("No users imported from VCF.")

    os.remove(vcf_path)  # Clean up

async def main():
    await client.start(phone=phone)
    load_stored_users()
    print("Userbot is running. As admin, message /upload_vcf to upload VCF. Users can use /start.")
    # Register the receive_vcf as a global handler for documents from owner
    client.add_event_handler(receive_vcf, events.NewMessage())
    await client.run_until_disconnected()

if __name__ == '__main__':
    client.loop.run_until_complete(main())
