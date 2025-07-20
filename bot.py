# bot.py
#
# A Telegram bot that monitors Ethereum validators and node health, with
# automatic failover and on-demand status commands.
#
# V16 - Added /confirm command for on-demand health summary & fixed /logs line count.

import os
import logging
import requests
import asyncio
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError

# --- Configuration ---
load_dotenv()

# --- Logging Setup ---
LOG_FILE = "bot.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=1024 * 1024, backupCount=5),
        logging.StreamHandler()
    ]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Constants ---
EPOCHS_PER_SYNC_COMMITTEE_PERIOD = 256
UPCOMING_NOTIFICATION_EPOCH_THRESHOLD = 15
NODE_SYNC_TOLERANCE_BLOCKS = 3

# --- Environment Variable Loading ---
try:
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    
    PRIMARY_BEACON_URL = os.getenv("PRIMARY_BEACON_NODE_URL")
    PRIMARY_EXECUTION_URL = os.getenv("PRIMARY_EXECUTION_NODE_URL")
    FALLBACK_BEACON_URL = os.getenv("FALLBACK_BEACON_NODE_URL")
    FALLBACK_EXECUTION_URL = os.getenv("FALLBACK_EXECUTION_NODE_URL")

    VALIDATOR_INDICES_STR = os.getenv("VALIDATOR_INDICES")
    if not VALIDATOR_INDICES_STR:
        raise ValueError("VALIDATOR_INDICES is not set in the .env file.")
    VALIDATOR_INDICES = [v.strip() for v in VALIDATOR_INDICES_STR.split(',')]
    CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "12"))
except Exception as e:
    logger.critical(f"Error loading environment variables: {e}. Please check .env file.")
    exit(1)

# --- API Configuration ---
HEADERS = {"Accept": "application/json"}
JSON_RPC_HEADERS = {"Content-Type": "application/json"}

# --- State Management ---
validator_last_status = {}
pending_proposals = {}
sync_duty_state = {}
node_health_state = {'primary': 'unknown', 'fallback': 'unknown'}

# --- Bot Command Handlers & Lifecycle ---

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the last 100 lines of the log file."""
    if str(update.effective_chat.id) != TELEGRAM_CHAT_ID: return
    try:
        with open(LOG_FILE, 'r') as f:
            lines = f.readlines()
        last_lines = lines[-100:] # Corrected to 100 lines
        log_text = "".join(last_lines).replace("`", "'")
        if not log_text:
            log_text = "Log file empty."
        await update.message.reply_text(f"```\n{log_text}\n```", parse_mode='MarkdownV2')
    except Exception as e:
        await update.message.reply_text(f"Error reading logs: {e}")

async def confirm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs an on-demand health and validator status check."""
    if str(update.effective_chat.id) != TELEGRAM_CHAT_ID: return
    await update.message.reply_text("Running on-demand checks, please wait...")

    # Check nodes
    primary_health = await check_node_health(PRIMARY_BEACON_URL, PRIMARY_EXECUTION_URL)
    fallback_health = await check_node_health(FALLBACK_BEACON_URL, FALLBACK_EXECUTION_URL)

    # Check validators against the primary node
    validator_summary = await get_validator_summary(PRIMARY_BEACON_URL)

    # Build response message
    primary_status = primary_health.get('status', 'N/A').replace('_', ' ').title()
    fallback_status = fallback_health.get('status', 'N/A').replace('_', ' ').title()
    
    val_message = "Could not fetch validator status."
    if validator_summary:
        val_message = f"{validator_summary['active_count']}/{validator_summary['total_count']} validators are active."

    summary_message = (
        f"*On-Demand Status Report*\n\n"
        f"Validators: {val_message}\n"
        f"Primary Node: *{primary_status}*\n"
        f"Fallback Node: *{fallback_status}*"
    )
    await update.message.reply_text(summary_message, parse_mode='Markdown')


async def send_telegram_message(bot: Bot, message: str):
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
        logger.info(f"Sent message: {message}")
    except TelegramError as e:
        logger.error(f"Failed to send Telegram message: {e}")

async def post_init(application: Application):
    await send_telegram_message(application.bot, "✅ *Validator Monitor Bot has started*")
    logger.info("Bot started. Awaiting commands and running checks...")

async def post_shutdown(application: Application):
    logger.info("Bot is shutting down...")
    await send_telegram_message(application.bot, " Validator Monitor Bot has been stopped.")
    logger.info("Bot stopped gracefully.")

# --- Node Health & Core Logic ---

async def check_node_health(beacon_url: str, execution_url: str) -> dict:
    if not beacon_url or not execution_url:
        return {'is_healthy': False, 'status': 'not_configured'}
    try:
        url = f"{beacon_url}/eth/v2/beacon/blocks/head"
        cl_response = requests.get(url, headers=HEADERS, timeout=5)
        cl_response.raise_for_status()
        cl_block_number = int(cl_response.json()['data']['message']['body']['execution_payload']['block_number'])
    except Exception as e:
        logger.warning(f"Health check failed for CL at {beacon_url}: {e}")
        return {'is_healthy': False, 'status': 'cl_unreachable'}
    try:
        payload = {"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}
        el_response = requests.post(execution_url, headers=JSON_RPC_HEADERS, json=payload, timeout=5)
        el_response.raise_for_status()
        el_block_number = int(el_response.json()['result'], 16)
    except Exception as e:
        logger.warning(f"Health check failed for EL at {execution_url}: {e}")
        return {'is_healthy': False, 'status': 'el_unreachable'}
    if abs(cl_block_number - el_block_number) > NODE_SYNC_TOLERANCE_BLOCKS:
        return {'is_healthy': False, 'status': 'out_of_sync'}
    return {'is_healthy': True, 'status': 'healthy'}

async def get_current_slot_and_epoch(beacon_url: str):
    try:
        response = requests.get(f"{beacon_url}/eth/v1/beacon/headers/head", headers=HEADERS, timeout=5)
        response.raise_for_status()
        slot = int(response.json()['data']['header']['message']['slot'])
        return slot, slot // 32
    except Exception:
        return None, None

# --- Validator Check Functions ---

async def get_validator_summary(beacon_url: str) -> dict | None:
    """Fetches validator statuses and returns a count."""
    if not beacon_url: return None
    try:
        url = f"{beacon_url}/eth/v1/beacon/states/head/validators?id={','.join(VALIDATOR_INDICES)}"
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        validators_data = response.json()['data']
        active_count = sum(1 for info in validators_data if "active" in info['status'])
        return {'total_count': len(validators_data), 'active_count': active_count}
    except Exception as e:
        logger.error(f"Error getting validator summary from {beacon_url}: {e}")
        return None

async def check_validator_status(bot: Bot, beacon_url: str):
    """Checks for status changes and sends alerts."""
    try:
        url = f"{beacon_url}/eth/v1/beacon/states/head/validators?id={','.join(VALIDATOR_INDICES)}"
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        for info in response.json()['data']:
            index, status = str(info['index']), info['status']
            if "active" not in status and "active" in validator_last_status.get(index, "active"):
                await send_telegram_message(bot, f"🚨 *VALIDATOR OFFLINE* 🚨\n\nIndex: `{index}`\nStatus: `{status.replace('_', ' ').title()}`")
            validator_last_status[index] = status
    except Exception as e:
        logger.error(f"Error checking validator status: {e}")

# ... (All other validator check functions like proposals and sync duties remain the same) ...
async def check_confirmed_proposals(bot: Bot, current_slot: int, beacon_url: str):
    proposals_to_remove = []
    for slot, info in list(pending_proposals.items()):
        if int(slot) == current_slot - 1:
            proposals_to_remove.append(slot)
            validator_index = info['validator_index']
            logger.info(f"Checking confirmation for slot {slot} by {validator_index} on {beacon_url}")
            try:
                url = f"{beacon_url}/eth/v2/beacon/blocks/{slot}"
                response = requests.get(url, headers=HEADERS, timeout=10)
                if response.status_code == 404:
                    await send_telegram_message(bot, f"❌ *MISSED PROPOSAL* ❌\n\nValidator `{validator_index}` missed proposal at slot `{slot}`.")
                    continue
                response.raise_for_status()
                data = response.json()['data']
                graffiti = bytes.fromhex(data['message']['body']['graffiti'].replace('0x', '')).decode('utf-8', 'ignore').strip()
                payload = data['message']['body'].get('execution_payload', {})
                value_eth = int(payload.get('value', '0')) / 1e18
                await send_telegram_message(bot, f"🎉 *PROPOSAL CONFIRMED* 🎉\n\nValidator `{validator_index}` proposed block at slot `{slot}`.\n💰 *MEV Rewards:* `{value_eth:.6f} ETH`\n🛰️ *Relay:* `{graffiti}`")
            except Exception as e:
                logger.error(f"Error confirming proposal for slot {slot}: {e}")
    for slot in proposals_to_remove:
        if slot in pending_proposals: del pending_proposals[slot]

async def check_upcoming_proposals(bot: Bot, current_epoch: int, beacon_url: str):
    monitored_set = set(VALIDATOR_INDICES)
    try:
        url = f"{beacon_url}/eth/v1/validator/duties/proposer/{current_epoch}"
        response = requests.get(url, headers=HEADERS, timeout=5)
        response.raise_for_status()
        for proposal in response.json()['data']:
            slot, index = str(proposal['slot']), str(proposal['validator_index'])
            if index in monitored_set and slot not in pending_proposals:
                pending_proposals[slot] = {'validator_index': index}
                await send_telegram_message(bot, f"🔔 *UPCOMING PROPOSAL* 🔔\n\nValidator `{index}` to propose block at slot `{slot}`.")
    except Exception as e:
        logger.error(f"Error checking for upcoming proposals: {e}")

async def check_sync_duties(bot: Bot, current_epoch: int, beacon_url: str):
    monitored_set = set(VALIDATOR_INDICES)
    next_period_start_epoch = ((current_epoch // EPOCHS_PER_SYNC_COMMITTEE_PERIOD) + 1) * EPOCHS_PER_SYNC_COMMITTEE_PERIOD
    if not any(key[1] == next_period_start_epoch for key in sync_duty_state):
        try:
            url = f"{beacon_url}/eth/v1/validator/duties/sync/{next_period_start_epoch}"
            response = requests.get(url, headers=HEADERS, timeout=10)
            if response.status_code != 404:
                response.raise_for_status()
                for duty in response.json()['data']:
                    index = str(duty['validator_index'])
                    if index in monitored_set:
                        duty_key = (index, next_period_start_epoch)
                        if duty_key not in sync_duty_state:
                            sync_duty_state[duty_key] = {'end_epoch': next_period_start_epoch + EPOCHS_PER_SYNC_COMMITTEE_PERIOD, 'notified_initial': False, 'notified_upcoming': False, 'notified_end': False}
        except Exception as e:
            logger.error(f"API Error fetching upcoming sync duties: {e}")
    for duty_key, state in list(sync_duty_state.items()):
        validator_index, start_epoch = duty_key
        end_epoch, epochs_until_start = state['end_epoch'], start_epoch - current_epoch
        if not state['notified_initial']:
            await send_telegram_message(bot, f"✅ *New Sync Duty Assigned*\n\nValidator `{validator_index}` duty: `{start_epoch}` to `{end_epoch}`")
            state['notified_initial'] = True
        if not state['notified_upcoming'] and 0 < epochs_until_start <= UPCOMING_NOTIFICATION_EPOCH_THRESHOLD:
            await send_telegram_message(bot, f"⏰ *Upcoming Sync Duty*\n\nValidator `{validator_index}` starts in `{epochs_until_start}` epochs (~{epochs_until_start * 6.4:.1f} mins).")
            state['notified_upcoming'] = True
        if not state['notified_end'] and current_epoch > end_epoch:
            await send_telegram_message(bot, f"🏁 *Sync Duty Ended*\n\nDuty for validator `{validator_index}` (started `{start_epoch}`) has ended.")
            state['notified_end'] = True
        if state['notified_end']:
            del sync_duty_state[duty_key]


async def run_validator_checks(context: ContextTypes.DEFAULT_TYPE, beacon_url: str):
    """Runs all validator-specific checks against a healthy node."""
    current_slot, current_epoch = await get_current_slot_and_epoch(beacon_url)
    if not current_slot: return
    logger.info(f"--- Running validator checks on {beacon_url} for slot {current_slot} ---")
    await check_confirmed_proposals(context.bot, current_slot, beacon_url)
    await check_upcoming_proposals(context.bot, current_epoch, beacon_url)
    if current_slot % 5 == 0:
        await check_validator_status(context.bot, beacon_url)
    if current_slot % 32 == 0:
        await check_sync_duties(context.bot, current_epoch, beacon_url)
    logger.info(f"--- Finished validator checks on {beacon_url} ---")

async def health_check_and_monitor(context: ContextTypes.DEFAULT_TYPE):
    """Main job: checks node health, fails over, and runs validator checks."""
    bot = context.bot
    active_beacon_url = None
    primary_health = await check_node_health(PRIMARY_BEACON_URL, PRIMARY_EXECUTION_URL)
    if primary_health['status'] != node_health_state['primary']:
        status_msg = primary_health.get('status', 'N/A').replace('_', ' ').title()
        if primary_health['is_healthy']:
            await send_telegram_message(bot, f"✅ *Primary Node Recovered*\nStatus: {status_msg}")
        else:
            await send_telegram_message(bot, f"🚨 *Primary Node Unhealthy*\nStatus: {status_msg}")
        node_health_state['primary'] = primary_health['status']
    if primary_health['is_healthy']:
        active_beacon_url = PRIMARY_BEACON_URL
    else:
        logger.warning("Primary node unhealthy. Checking fallback.")
        fallback_health = await check_node_health(FALLBACK_BEACON_URL, FALLBACK_EXECUTION_URL)
        if fallback_health['status'] != node_health_state['fallback']:
            status_msg = fallback_health.get('status', 'N/A').replace('_', ' ').title()
            if fallback_health['is_healthy']:
                await send_telegram_message(bot, f"✅ *Failing over to Fallback Node*\nStatus: {status_msg}")
            else:
                await send_telegram_message(bot, f"🚨 *Fallback Node Unhealthy*\nStatus: {status_msg}")
            node_health_state['fallback'] = fallback_health['status']
        if fallback_health['is_healthy']:
            active_beacon_url = FALLBACK_BEACON_URL
        else:
            logger.error("Both primary and fallback nodes are unhealthy.")
    if active_beacon_url:
        await run_validator_checks(context, active_beacon_url)
    else:
        logger.info("No healthy node available. Skipping validator checks.")

def main() -> None:
    """Initializes and runs the bot application."""
    logger.info("Starting validator monitor bot...")
    if not PRIMARY_BEACON_URL or not PRIMARY_EXECUTION_URL:
        logger.critical("Primary node URLs are not set in .env file. Exiting.")
        return
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("logs", logs_command))
    application.add_handler(CommandHandler("confirm", confirm_command))
    application.job_queue.run_repeating(health_check_and_monitor, interval=CHECK_INTERVAL_SECONDS, first=1)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
