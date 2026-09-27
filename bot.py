import os
import re
import logging
import asyncio
import time
import httpx
import psycopg2
import hashlib
import base64
import json
import traceback
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from psycopg2.extras import RealDictCursor, Json
from flask import Flask, request, jsonify
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, InlineQueryHandler
from telegram.helpers import escape_markdown

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

PER_PAGE = 10
ITEMS_PER_PAGE = 10
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
MAX_MESSAGE_LENGTH = 4000
DEFAULT_APPROVAL_GROUP_ID = None
TASK_INTERVAL_SECONDS = 20
MAX_REJECT_WARNINGS = 5
MAX_WRONG_WARNINGS = 3
MAX_TOTAL_WARNINGS = 15
LOCK_DURATION_SECONDS = 24 * 60 * 60
TASK_TIMEOUT_SECONDS = 2 * 60 * 60
MAX_TASK_AGE_HOURS = 24
DEFAULT_TIMEZONE = "Europe/London"

# M-Pesa B2C Configuration
MPESA_CONSUMER_KEY = os.environ.get("MPESA_CONSUMER_KEY")
MPESA_CONSUMER_SECRET = os.environ.get("MPESA_CONSUMER_SECRET")
MPESA_SHORTCODE = "4327163"
MPESA_INITIATOR_NAME = "EarnmilesAPI"
MPESA_SECURITY_CREDENTIAL = os.environ.get("MPESA_SECURITY_CREDENTIAL")
MPESA_COMMAND_ID = "SalaryPayment"
MPESA_ENVIRONMENT = "production"
MPESA_B2C_CALLBACK_BASE = os.environ.get("MPESA_CALLBACK_BASE", os.environ.get("CALLBACK_BASE", "https://a50666-5b96.c.onjrnm.link")).rstrip("/")

MPESA_AUTH_URL = "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
MPESA_B2C_URL = "https://api.safaricom.co.ke/mpesa/b2c/v1/paymentrequest"

MPESA_RESULT_URL = f"{MPESA_B2C_CALLBACK_BASE}/mpesa/result"
MPESA_TIMEOUT_URL = f"{MPESA_B2C_CALLBACK_BASE}/mpesa/timeout"

BOT_NAME = None


WELCOME_MESSAGE = """💎✨ WELCOME TO INBOXFORGE ELITE ✨💎

🚀 The #1 Premium Gmail Task Platform

💵 Earn $0.08 - $0.25 per completed task 💵
⚡️ Instant task delivery • Fast review • Quick payouts

🧩 How It Works:
╭─────────────────╮
│ 1️⃣ Tap Get New Job
│ 2️⃣ Create Gmail with given data
│ 3️⃣ Submit proof & earn
╰─────────────────╯

💠 Trusted by 50k+ workers
🔐 Secure & Anonymous

👇 Hit Start Guide below to begin your journey!"""

HOW_TO_REGISTER_TEXT = """🌟🔥 INBOXFORGE TASK GUIDE 🔥🌟

┏━━━━━━━━━━━━━━━━━━━━━┓
┃ 💼 COMPLETE TUTORIAL
┗━━━━━━━━━━━━━━━━━━━━━┛

➊ 📲 OPEN GMAIL APP
   • Launch Gmail on your phone
   • Install if not available

➋ ➕ ADD NEW ACCOUNT
   • Click your profile pic (top right)
   • Select Add another account
   • Choose Google 🌐

➌ ✨ CREATE NEW
   • Tap Create Account
   • Choose For my personal use 🙋

➍ 🙍 NAME SETUP
   • First Name: Use name from task 📝
   • Last Name: ⚠️ LEAVE BLANK
   • Hit Next ➡️

➎ 🎂 BIRTH & GENDER
   • Use any DOB (18+ only)
   • Gender: Prefer not to say
   • Next ➡️

➏ 📧 USERNAME
   • Select Create your own Gmail address
   • Type EXACT email from job task
   • Next ➡️

➐ 🔑 PASSWORD
   • Use EXACT password from job
   • Confirm & Next ➡️

➑ 📵 PHONE SKIP
   • ⛔ DON'T add phone
   • Tap Skip ⏭️

➒ 📨 RECOVERY SKIP
   • ⛔ DON'T add recovery
   • Tap Skip ⏭️

➓ 📜 AGREEMENT
   • Scroll to bottom
   • Tap I Agree ✅

┏━━━━━━━━━━━━━━━━━━━━━┓
┃ 🎉 DONE! ACCOUNT READY
┗━━━━━━━━━━━━━━━━━━━━━┛

⚠️ GOLDEN RULES:
✅ Exact data from task only
🚫 No last name
🚫 No phone number
🚫 No recovery email
⚡️ Submit fast + Logout

💎 Good luck! 💎"""


MAIN_MENU_BUTTONS = [
    ["⚡ Start Earning", "👑 My Dashboard"],
    ["💎 My Wallet", "🎁 Bonus Hub"],
    ["🔧 Control Panel", "📞 Help Center"]
]

app = Flask(__name__)
application = None
loop = None

PAYMENT_METHODS = {
    "mpesa": {
        "name": "📱 MPESA",
        "address_format": "Phone number (254712345678)",
        "address_regex": r"^254\d{9}$",
        "address_example": "254712345678",
        "fee_percentage": 10.0
    },
    "paypal": {
        "name": "💳 PayPal",
        "address_format": "PayPal email address",
        "address_regex": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
        "address_example": "user@email.com",
        "fee_percentage": 23.0
    },
    "litecoin": {
        "name": "⚡ Litecoin (LTC)",
        "address_format": "LTC wallet address",
        "address_regex": r"^[LM3][a-km-zA-HJ-NP-Z1-9]{26,33}$",
        "address_example": "LbTjMGN7gELw4KbeyQf6cTCq859hD18guE",
        "fee_percentage": 23.0
    },
    "pep20": {
        "name": "💎 USD PEP20",
        "address_format": "PEP20 wallet address",
        "address_regex": r"^0x[a-fA-F0-9]{40}$",
        "address_example": "0x1234567890abcdef1234567890abcdef12345678",
        "fee_percentage": 23.0
    }
}

PAYMENT_METHODS_LIST = list(PAYMENT_METHODS.keys())

_broadcast_running = False
_last_broadcast_time = 0
_broadcast_lock = asyncio.Lock()
BROADCAST_COOLDOWN = 300

_processed_callbacks = set()
_CALLBACK_CACHE_MAX_SIZE = 1000
_CALLBACK_CACHE_EXPIRY = 3600

def round_down_amount(amount: float, decimals: int = 0) -> float:
    multiplier = 10 ** decimals
    return int(amount * multiplier) / multiplier

def get_mpesa_amount(usd_amount: float, usd_to_kes_rate: float = 112.0) -> int:
    # Fixed rate: 1 USD = 112 KES
    FIXED_RATE = 112.0
    kes_amount = usd_amount * FIXED_RATE
    rounded_amount = int(kes_amount)
    logger.info(f"Amount conversion: ${usd_amount:.2f} USD → {kes_amount:.2f} KES → {rounded_amount} KES (fixed rate 112)")
    return rounded_amount

def get_timezone_offset(tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
        offset_sec = now.utcoffset().total_seconds()
        hours = int(offset_sec // 3600)
        minutes = int(abs(offset_sec % 3600) // 60)
        sign = '+' if hours >= 0 else ''
        return f"GMT{sign}{hours:02d}:{minutes:02d}"
    except Exception:
        return "GMT+00:00"

def format_timezone_with_offset(tz_name: str) -> str:
    try:
        offset = get_timezone_offset(tz_name)
        display_name = tz_name.replace('_', ' ').replace('/', ' / ')
        return f"{display_name} {offset}"
    except Exception:
        return f"{tz_name} GMT+00:00"

ALL_TIMEZONES = {
    "Africa": [
        "Africa/Abidjan", "Africa/Accra", "Africa/Addis_Ababa", "Africa/Algiers",
        "Africa/Asmara", "Africa/Bamako", "Africa/Bangui", "Africa/Banjul",
        "Africa/Bissau", "Africa/Blantyre", "Africa/Brazzaville", "Africa/Bujumbura",
        "Africa/Cairo", "Africa/Casablanca", "Africa/Ceuta", "Africa/Conakry",
        "Africa/Dakar", "Africa/Dar_es_Salaam", "Africa/Djibouti", "Africa/Douala",
        "Africa/El_Aaiun", "Africa/Freetown", "Africa/Gaborone", "Africa/Harare",
        "Africa/Johannesburg", "Africa/Juba", "Africa/Kampala", "Africa/Khartoum",
        "Africa/Kigali", "Africa/Kinshasa", "Africa/Lagos", "Africa/Libreville",
        "Africa/Lome", "Africa/Luanda", "Africa/Lubumbashi", "Africa/Lusaka",
        "Africa/Malabo", "Africa/Maputo", "Africa/Maseru", "Africa/Mbabane",
        "Africa/Mogadishu", "Africa/Monrovia", "Africa/Nairobi", "Africa/Ndjamena",
        "Africa/Niamey", "Africa/Nouakchott", "Africa/Ouagadougou", "Africa/Porto-Novo",
        "Africa/Sao_Tome", "Africa/Tripoli", "Africa/Tunis", "Africa/Windhoek"
    ],
    "America": [
        "America/Adak", "America/Anchorage", "America/Anguilla", "America/Antigua",
        "America/Araguaina", "America/Argentina/Buenos_Aires", "America/Argentina/Catamarca",
        "America/Argentina/Cordoba", "America/Argentina/Jujuy", "America/Argentina/La_Rioja",
        "America/Argentina/Mendoza", "America/Argentina/Rio_Gallegos", "America/Argentina/Salta",
        "America/Argentina/San_Juan", "America/Argentina/San_Luis", "America/Argentina/Tucuman",
        "America/Argentina/Ushuaia", "America/Aruba", "America/Asuncion", "America/Atikokan",
        "America/Bahia", "America/Bahia_Banderas", "America/Barbados", "America/Belem",
        "America/Belize", "America/Blanc-Sablon", "America/Boa_Vista", "America/Bogota",
        "America/Boise", "America/Cambridge_Bay", "America/Campo_Grande", "America/Cancun",
        "America/Caracas", "America/Cayenne", "America/Cayman", "America/Chicago",
        "America/Chihuahua", "America/Costa_Rica", "America/Creston", "America/Cuiaba",
        "America/Curacao", "America/Danmarkshavn", "America/Dawson", "America/Dawson_Creek",
        "America/Denver", "America/Detroit", "America/Dominica", "America/Edmonton",
        "America/Eirunepe", "America/El_Salvador", "America/Fort_Nelson", "America/Fortaleza",
        "America/Glace_Bay", "America/Godthab", "America/Goose_Bay", "America/Grand_Turk",
        "America/Grenada", "America/Guadeloupe", "America/Guatemala", "America/Guayaquil",
        "America/Guyana", "America/Halifax", "America/Havana", "America/Hermosillo",
        "America/Indiana/Indianapolis", "America/Indiana/Knox", "America/Indiana/Marengo",
        "America/Indiana/Petersburg", "America/Indiana/Tell_City", "America/Indiana/Vevay",
        "America/Indiana/Vincennes", "America/Indiana/Winamac", "America/Inuvik",
        "America/Iqaluit", "America/Jamaica", "America/Juneau", "America/Kentucky/Louisville",
        "America/Kentucky/Monticello", "America/Kralendijk", "America/La_Paz",
        "America/Lima", "America/Los_Angeles", "America/Lower_Princes", "America/Maceio",
        "America/Managua", "America/Manaus", "America/Marigot", "America/Martinique",
        "America/Matamoros", "America/Mazatlan", "America/Menominee", "America/Merida",
        "America/Metlakatla", "America/Mexico_City", "America/Miquelon", "America/Moncton",
        "America/Monterrey", "America/Montevideo", "America/Montserrat", "America/Nassau",
        "America/New_York", "America/Nipigon", "America/Nome", "America/Noronha",
        "America/North_Dakota/Beulah", "America/North_Dakota/Center", "America/North_Dakota/Salem",
        "America/Nuuk", "America/Ojinaga", "America/Panama", "America/Pangnirtung",
        "America/Paramaribo", "America/Phoenix", "America/Port-au-Prince", "America/Port_of_Spain",
        "America/Porto_Velho", "America/Puerto_Rico", "America/Punta_Arenas",
        "America/Rainy_River", "America/Rankin_Inlet", "America/Recife", "America/Regina",
        "America/Resolute", "America/Rio_Branco", "America/Santarem", "America/Santiago",
        "America/Santo_Domingo", "America/Sao_Paulo", "America/Scoresbysund",
        "America/Sitka", "America/St_Barthelemy", "America/St_Johns", "America/St_Kitts",
        "America/St_Lucia", "America/St_Thomas", "America/St_Vincent", "America/Swift_Current",
        "America/Tegucigalpa", "America/Thule", "America/Thunder_Bay", "America/Tijuana",
        "America/Toronto", "America/Tortola", "America/Vancouver", "America/Whitehorse",
        "America/Winnipeg", "America/Yakutat", "America/Yellowknife"
    ],
    "Antarctica": [
        "Antarctica/Casey", "Antarctica/Davis", "Antarctica/DumontDUrville",
        "Antarctica/Macquarie", "Antarctica/Mawson", "Antarctica/McMurdo",
        "Antarctica/Palmer", "Antarctica/Rothera", "Antarctica/Syowa", "Antarctica/Troll",
        "Antarctica/Vostok"
    ],
    "Asia": [
        "Asia/Aden", "Asia/Almaty", "Asia/Amman", "Asia/Anadyr", "Asia/Aqtau",
        "Asia/Aqtobe", "Asia/Ashgabat", "Asia/Atyrau", "Asia/Baghdad", "Asia/Bahrain",
        "Asia/Baku", "Asia/Bangkok", "Asia/Barnaul", "Asia/Beirut", "Asia/Bishkek",
        "Asia/Brunei", "Asia/Chita", "Asia/Choibalsan", "Asia/Colombo", "Asia/Damascus",
        "Asia/Dhaka", "Asia/Dili", "Asia/Dubai", "Asia/Dushanbe", "Asia/Famagusta",
        "Asia/Gaza", "Asia/Hebron", "Asia/Ho_Chi_Minh", "Asia/Hong_Kong", "Asia/Hovd",
        "Asia/Irkutsk", "Asia/Jakarta", "Asia/Jayapura", "Asia/Jerusalem", "Asia/Kabul",
        "Asia/Kamchatka", "Asia/Karachi", "Asia/Kathmandu", "Asia/Khandyga",
        "Asia/Kolkata", "Asia/Krasnoyarsk", "Asia/Kuala_Lumpur", "Asia/Kuching",
        "Asia/Kuwait", "Asia/Macau", "Asia/Magadan", "Asia/Makassar", "Asia/Manila",
        "Asia/Muscat", "Asia/Nicosia", "Asia/Novokuznetsk", "Asia/Novosibirsk",
        "Asia/Omsk", "Asia/Oral", "Asia/Phnom_Penh", "Asia/Pontianak", "Asia/Pyongyang",
        "Asia/Qatar", "Asia/Qostanay", "Asia/Qyzylorda", "Asia/Riyadh", "Asia/Sakhalin",
        "Asia/Samarkand", "Asia/Seoul", "Asia/Shanghai", "Asia/Singapore", "Asia/Srednekolymsk",
        "Asia/Taipei", "Asia/Tashkent", "Asia/Tbilisi", "Asia/Tehran", "Asia/Thimphu",
        "Asia/Tokyo", "Asia/Tomsk", "Asia/Ulaanbaatar", "Asia/Urumqi", "Asia/Ust-Nera",
        "Asia/Vientiane", "Asia/Vladivostok", "Asia/Yakutsk", "Asia/Yangon",
        "Asia/Yekaterinburg", "Asia/Yerevan"
    ],
    "Atlantic": [
        "Atlantic/Azores", "Atlantic/Bermuda", "Atlantic/Canary", "Atlantic/Cape_Verde",
        "Atlantic/Faroe", "Atlantic/Madeira", "Atlantic/Reykjavik", "Atlantic/South_Georgia",
        "Atlantic/St_Helena", "Atlantic/Stanley"
    ],
    "Australia": [
        "Australia/Adelaide", "Australia/Brisbane", "Australia/Broken_Hill",
        "Australia/Darwin", "Australia/Eucla", "Australia/Hobart", "Australia/Lindeman",
        "Australia/Lord_Howe", "Australia/Melbourne", "Australia/Perth", "Australia/Sydney"
    ],
    "Europe": [
        "Europe/Amsterdam", "Europe/Andorra", "Europe/Astrakhan", "Europe/Athens",
        "Europe/Belgrade", "Europe/Berlin", "Europe/Bratislava", "Europe/Brussels",
        "Europe/Bucharest", "Europe/Budapest", "Europe/Busingen", "Europe/Chisinau",
        "Europe/Copenhagen", "Europe/Dublin", "Europe/Gibraltar", "Europe/Guernsey",
        "Europe/Helsinki", "Europe/Isle_of_Man", "Europe/Istanbul", "Europe/Jersey",
        "Europe/Kaliningrad", "Europe/Kiev", "Europe/Kirov", "Europe/Lisbon",
        "Europe/Ljubljana", "Europe/London", "Europe/Luxembourg", "Europe/Madrid",
        "Europe/Malta", "Europe/Mariehamn", "Europe/Minsk", "Europe/Monaco",
        "Europe/Moscow", "Europe/Oslo", "Europe/Paris", "Europe/Podgorica",
        "Europe/Prague", "Europe/Riga", "Europe/Rome", "Europe/Samara", "Europe/San_Marino",
        "Europe/Sarajevo", "Europe/Saratov", "Europe/Simferopol", "Europe/Skopje",
        "Europe/Sofia", "Europe/Stockholm", "Europe/Tallinn", "Europe/Tirane",
        "Europe/Ulyanovsk", "Europe/Uzhgorod", "Europe/Vaduz", "Europe/Vatican",
        "Europe/Vienna", "Europe/Vilnius", "Europe/Volgograd", "Europe/Warsaw",
        "Europe/Zagreb", "Europe/Zaporozhye", "Europe/Zurich"
    ],
    "Indian": [
        "Indian/Antananarivo", "Indian/Chagos", "Indian/Christmas", "Indian/Cocos",
        "Indian/Comoro", "Indian/Kerguelen", "Indian/Mahe", "Indian/Maldives",
        "Indian/Mauritius", "Indian/Mayotte", "Indian/Reunion"
    ],
    "Pacific": [
        "Pacific/Apia", "Pacific/Auckland", "Pacific/Bougainville", "Pacific/Chatham",
        "Pacific/Chuuk", "Pacific/Easter", "Pacific/Efate", "Pacific/Enderbury",
        "Pacific/Fakaofo", "Pacific/Fiji", "Pacific/Funafuti", "Pacific/Galapagos",
        "Pacific/Gambier", "Pacific/Guadalcanal", "Pacific/Guam", "Pacific/Honolulu",
        "Pacific/Kiritimati", "Pacific/Kosrae", "Pacific/Kwajalein", "Pacific/Majuro",
        "Pacific/Marquesas", "Pacific/Midway", "Pacific/Nauru", "Pacific/Niue",
        "Pacific/Norfolk", "Pacific/Noumea", "Pacific/Pago_Pago", "Pacific/Palau",
        "Pacific/Pitcairn", "Pacific/Pohnpei", "Pacific/Port_Moresby", "Pacific/Rarotonga",
        "Pacific/Saipan", "Pacific/Tahiti", "Pacific/Tarawa", "Pacific/Tongatapu",
        "Pacific/Wake", "Pacific/Wallis"
    ],
    "Arctic": [
        "Arctic/Longyearbyen"
    ],
    "UTC": [
        "UTC"
    ]
}

ALL_TIMEZONES_WITH_OFFSETS = {}
for continent, zones in ALL_TIMEZONES.items():
    ALL_TIMEZONES_WITH_OFFSETS[continent] = [format_timezone_with_offset(zone) for zone in zones]

for continent in ALL_TIMEZONES_WITH_OFFSETS:
    ALL_TIMEZONES_WITH_OFFSETS[continent].sort()

CURRENCIES = {
    "USD": "US Dollar", "AED": "United Arab Emirates Dirham", "AFN": "Afghan Afghani",
    "ALL": "Albanian Lek", "AMD": "Armenian Dram", "ANG": "Netherlands Antillean Guilder",
    "AOA": "Angolan Kwanza", "ARS": "Argentine Peso", "AUD": "Australian Dollar",
    "AWG": "Aruban Florin", "AZN": "Azerbaijani Manat", "BAM": "Bosnia-Herzegovina Convertible Mark",
    "BBD": "Barbadian Dollar", "BDT": "Bangladeshi Taka", "BGN": "Bulgarian Lev",
    "BHD": "Bahraini Dinar", "BIF": "Burundian Franc", "BMD": "Bermudian Dollar",
    "BND": "Brunei Dollar", "BOB": "Bolivian Boliviano", "BRL": "Brazilian Real",
    "BSD": "Bahamian Dollar", "BTC": "Bitcoin", "BTN": "Bhutanese Ngultrum",
    "BWP": "Botswana Pula", "BYN": "Belarusian Ruble", "BYR": "Belarusian Ruble (old)",
    "BZD": "Belize Dollar", "CAD": "Canadian Dollar", "CDF": "Congolese Franc",
    "CHF": "Swiss Franc", "CLF": "Chilean Unit of Account", "CLP": "Chilean Peso",
    "CNY": "Chinese Yuan", "COP": "Colombian Peso", "CRC": "Costa Rican Colón",
    "CUC": "Cuban Convertible Peso", "CUP": "Cuban Peso", "CVE": "Cape Verdean Escudo",
    "CZK": "Czech Koruna", "DJF": "Djiboutian Franc", "DKK": "Danish Krone",
    "DOP": "Dominican Peso", "DZD": "Algerian Dinar", "EGP": "Egyptian Pound",
    "ERN": "Eritrean Nakfa", "ETB": "Ethiopian Birr", "EUR": "Euro",
    "FJD": "Fijian Dollar", "FKP": "Falkland Islands Pound", "GBP": "British Pound",
    "GEL": "Georgian Lari", "GGP": "Guernsey Pound", "GHS": "Ghanaian Cedi",
    "GIP": "Gibraltar Pound", "GMD": "Gambian Dalasi", "GNF": "Guinean Franc",
    "GTQ": "Guatemalan Quetzal", "GYD": "Guyanese Dollar", "HKD": "Hong Kong Dollar",
    "HNL": "Honduran Lempira", "HRK": "Croatian Kuna", "HTG": "Haitian Gourde",
    "HUF": "Hungarian Forint", "IDR": "Indonesian Rupiah", "ILS": "Israeli Shekel",
    "IMP": "Isle of Man Pound", "INR": "Indian Rupee", "IQD": "Iraqi Dinar",
    "IRR": "Iranian Rial", "ISK": "Icelandic Króna", "JEP": "Jersey Pound",
    "JMD": "Jamaican Dollar", "JOD": "Jordanian Dinar", "JPY": "Japanese Yen",
    "KES": "Kenyan Shilling", "KGS": "Kyrgyzstani Som", "KHR": "Cambodian Riel",
    "KMF": "Comorian Franc", "KPW": "North Korean Won", "KRW": "South Korean Won",
    "KWD": "Kuwaiti Dinar", "KYD": "Cayman Islands Dollar", "KZT": "Kazakhstani Tenge",
    "LAK": "Lao Kip", "LBP": "Lebanese Pound", "LKR": "Sri Lankan Rupee",
    "LRD": "Liberian Dollar", "LSL": "Lesotho Loti", "LTL": "Lithuanian Litas",
    "LVL": "Latvian Lats", "LYD": "Libyan Dinar", "MAD": "Moroccan Dirham",
    "MDL": "Moldovan Leu", "MGA": "Malagasy Ariary", "MKD": "Macedonian Denar",
    "MMK": "Myanmar Kyat", "MNT": "Mongolian Tögrög", "MOP": "Macanese Pataca",
    "MRO": "Mauritanian Ouguiya", "MUR": "Mauritian Rupee", "MVR": "Maldivian Rufiyaa",
    "MWK": "Malawian Kwacha", "MXN": "Mexican Peso", "MYR": "Malaysian Ringgit",
    "MZN": "Mozambican Metical", "NAD": "Namibian Dollar", "NGN": "Nigerian Naira",
    "NIO": "Nicaraguan Córdoba", "NOK": "Norwegian Krone", "NPR": "Nepalese Rupee",
    "NZD": "New Zealand Dollar", "OMR": "Omani Rial", "PAB": "Panamanian Balboa",
    "PEN": "Peruvian Sol", "PGK": "Papua New Guinean Kina", "PHP": "Philippine Peso",
    "PKR": "Pakistani Rupee", "PLN": "Polish Złoty", "PYG": "Paraguayan Guaraní",
    "QAR": "Qatari Rial", "RON": "Romanian Leu", "RSD": "Serbian Dinar",
    "RUB": "Russian Ruble", "RWF": "Rwandan Franc", "SAR": "Saudi Riyal",
    "SBD": "Solomon Islands Dollar", "SCR": "Seychellois Rupee", "SDG": "Sudanese Pound",
    "SEK": "Swedish Krona", "SGD": "Singapore Dollar", "SHP": "Saint Helena Pound",
    "SLL": "Sierra Leonean Leone", "SOS": "Somali Shilling", "SRD": "Surinamese Dollar",
    "STD": "São Tomé and Príncipe Dobra", "SVC": "Salvadoran Colón", "SYP": "Syrian Pound",
    "SZL": "Swazi Lilangeni", "THB": "Thai Baht", "TJS": "Tajikistani Somoni",
    "TMT": "Turkmenistani Manat", "TND": "Tunisian Dinar", "TOP": "Tongan Paʻanga",
    "TRY": "Turkish Lira", "TTD": "Trinidad and Tobago Dollar", "TWD": "New Taiwan Dollar",
    "TZS": "Tanzanian Shilling", "UAH": "Ukrainian Hryvnia", "UGX": "Ugandan Shilling",
    "UYU": "Uruguayan Peso", "UZS": "Uzbekistani Som", "VEF": "Venezuelan Bolívar",
    "VND": "Vietnamese Đồng", "VUV": "Vanuatu Vatu", "WST": "Samoan Tala",
    "XAF": "CFA Franc BEAC", "XAG": "Silver Ounce", "XAU": "Gold Ounce",
    "XCD": "East Caribbean Dollar", "XDR": "SDR (Special Drawing Rights)",
    "XOF": "CFA Franc BCEAO", "XPF": "CFP Franc", "YER": "Yemeni Rial",
    "ZAR": "South African Rand", "ZMK": "Zambian Kwacha (old)",
    "ZMW": "Zambian Kwacha", "ZWL": "Zimbabwean Dollar"
}

DEFAULT_USER = {
    "available_balance": 0.0,
    "onhold_balance": 0.0,
    "affiliate_onhold_balance": 0.0,
    "withdrawal_onhold_balance": 0.0,
    "active_task_id": None,
    "awaiting_new_task": False,
    "awaiting_withdraw_amount": False,
    "awaiting_mpesa": False,
    "pending_withdraw_amount": 0.0,
    "in_withdraw_menu": False,
    "in_balance_menu": False,
    "in_refer_menu": False,
    "in_profile_menu": False,
    "in_settings_menu": False,
    "in_support_menu": False,
    "last_task_time": 0,
    "referrals": [],
    "referred_by": None,
    "referral_paid": False,
    "referral_bonus_pending": False,
    "join_referral_paid": False,
    "warnings": 0,
    "wrong_warnings": 0,
    "locked_until": 0,
    "tasks_completed": 0,
    "timezone": DEFAULT_TIMEZONE,
    "currency": "USD",
    "awaiting_payment_method": False,
    "awaiting_payment_address": False,
    "selected_payment_method": None,
    "choosing_timezone_continent": False,
    "choosing_timezone": False,
    "selected_continent": None,
    "tasks_page": 0,
    "history_page": 0,
    "selecting_currency": False,
    "currencies_list": None,
    "currency_page": 0,
    "banned": False,
    "total_warnings": 0,
    "in_myaccount_menu": False,
    "in_activities_menu": False,
    "activities_page": 0,
    "referred_users_completed": [],
    "last_task_date": None,
    "daily_bonus_claimed": False,
    "awaiting_withdrawal_selection": False,
    "task_creator_admin": None
}

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def get_setting(key: str, default: str = "0") -> str:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM bot_settings WHERE key = %s;", (key,))
        result = cur.fetchone()
        if result:
            return result[0]
        return default
    except Exception as e:
        print(f"Error getting setting {key}: {e}")
        return default
    finally:
        cur.close()
        conn.close()

def get_setting_float(key: str, default: float = 0.0) -> float:
    try:
        return float(get_setting(key, str(default)))
    except ValueError:
        return default

def update_setting(key: str, value: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO bot_settings (key, value, updated_at) VALUES (%s, %s, CURRENT_TIMESTAMP) "
            "ON CONFLICT (key) DO UPDATE SET value = %s, updated_at = CURRENT_TIMESTAMP",
            (key, value, value)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating setting {key}: {e}")
        return False
    finally:
        cur.close()
        conn.close()

SETUP_KEYS_ORDER = [
    ('approval_group_id', 'approval group ID (e.g. -1001234567890)'),
    ('task_pay', 'task pay per task in USD (e.g. 0.10)'),
    ('withdrawal_limit', 'minimum withdrawal in USD (e.g. 0.70)'),
    ('bot_username', 'bot username without @ (e.g. myearningbot)'),
    ('announce_channel', 'announcement channel username or invite link'),
    ('support_bot', 'customer support bot URL or username'),
    ('bot_name', 'bot display name'),
]

def get_task_pay() -> float:
    return get_setting_float('task_pay', 0.0)
def get_withdrawal_limit() -> float:
    return get_setting_float('withdrawal_limit', 0.0)
def get_bot_username() -> str:
    return get_setting('bot_username', '')
def get_support_bot() -> str:
    return get_setting('support_bot', '')
def get_admin_id() -> str:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT admin_id FROM admin_settings WHERE is_super_admin = TRUE ORDER BY created_at LIMIT 1;")
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute("SELECT admin_id FROM admin_settings ORDER BY created_at LIMIT 1;")
        row = cur.fetchone()
        if row:
            return row[0]
        return None
    except Exception:
        return None
    finally:
        cur.close()
        conn.close()
def get_announce_channel() -> str:
    return get_setting('announce_channel', '')
def get_referral_bonus() -> float:
    return get_setting_float('referral_bonus', 0.0)
def get_bot_name() -> str:
    return get_setting('bot_name', 'ɢᴍᴀɪʟ ɴᴇᴛᴡᴏʀᴋ 🛜')
def get_daily_bonus() -> float:
    return get_setting_float('daily_bonus', 0.0)
def get_usd_to_kes_rate() -> float:
    rate = get_setting_float('usd_to_kes_rate', 112.0)
    # Fixed rate: 1 USD = 112 KES - if 0 or not set, force 112
    if not rate or rate == 0.0:
        return 112.0
    return 112.0  # Always return fixed 112 KES
def is_setup_completed() -> bool:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM admin_settings;")
        admin_count = cur.fetchone()[0]
        if admin_count == 0:
            cur.close()
            conn.close()
            return False
        required_keys = ['task_pay','withdrawal_limit','bot_username','announce_channel','support_bot']
        cur.execute("SELECT key FROM bot_settings WHERE key = ANY(%s);", (required_keys,))
        existing = {r[0] for r in cur.fetchall()}
        cur.close()
        conn.close()
        return all(k in existing for k in required_keys)
    except Exception:
        return False
def get_admin_approval_group(admin_id: str):
    if not admin_id:
        return None
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT approval_group_id FROM admin_settings WHERE admin_id = %s;", (admin_id,))
        result = cur.fetchone()
        if result and result[0]:
            return int(result[0])
        cur.execute("SELECT value FROM bot_settings WHERE key = %s;", ('approval_group_id',))
        r = cur.fetchone()
        if r:
            try:
                return int(r[0])
            except:
                return None
        return None
    except Exception as e:
        print(f"Error getting approval group for admin {admin_id}: {e}")
        return None
    finally:
        cur.close()
        conn.close()


def set_admin_approval_group(admin_id: str, group_id: int) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO admin_settings (admin_id, approval_group_id, updated_at) VALUES (%s, %s, CURRENT_TIMESTAMP) "
            "ON CONFLICT (admin_id) DO UPDATE SET approval_group_id = %s, updated_at = CURRENT_TIMESTAMP",
            (admin_id, group_id, group_id)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error setting approval group for admin {admin_id}: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def get_admin_username(admin_id: str) -> str:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT username FROM admin_settings WHERE admin_id = %s;", (admin_id,))
        result = cur.fetchone()
        if result and result[0]:
            return result[0]
        return None
    except Exception as e:
        print(f"Error getting username for admin {admin_id}: {e}")
        return None
    finally:
        cur.close()
        conn.close()

def set_admin_username(admin_id: str, username: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO admin_settings (admin_id, username, updated_at) VALUES (%s, %s, CURRENT_TIMESTAMP) "
            "ON CONFLICT (admin_id) DO UPDATE SET username = %s, updated_at = CURRENT_TIMESTAMP",
            (admin_id, username, username)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error setting username for admin {admin_id}: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def is_admin(user_id: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM admin_settings WHERE admin_id = %s;", (user_id,))
        return cur.fetchone() is not None
    except Exception as e:
        print(f"Error checking admin status for {user_id}: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def is_super_admin(user_id: str) -> bool:
    if not user_id:
        return False
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT is_super_admin FROM admin_settings WHERE admin_id = %s;", (user_id,))
        row = cur.fetchone()
        return bool(row and row[0])
    except Exception:
        return False
    finally:
        cur.close()
        conn.close()

def get_super_admin_approval_group():
    """Return the approval group configured specifically for the Super Admin."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT approval_group_id
            FROM admin_settings
            WHERE is_super_admin = TRUE
              AND approval_group_id IS NOT NULL
            ORDER BY created_at ASC
            LIMIT 1;
        """)
        row = cur.fetchone()
        return int(row[0]) if row and row[0] else None
    except Exception as e:
        logger.error(f"Error getting Super Admin approval group: {e}")
        return None
    finally:
        cur.close()
        conn.close()

def get_all_admins() -> list:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT admin_id FROM admin_settings ORDER BY admin_id;")
        return [row[0] for row in cur.fetchall()]
    except Exception as e:
        print(f"Error getting all admins: {e}")
        return []
    finally:
        cur.close()
        conn.close()

async def notify_all_admins_withdrawal(bot, message: str, parse_mode="Markdown", reply_markup=None, exclude_chat_id=None):
    """Legacy helper: route withdrawal notices ONLY to the Super Admin approval group."""
    if not bot:
        logger.warning("Skipping withdrawal notification: bot unavailable")
        return False
    approval_group_id = get_super_admin_approval_group()
    if not approval_group_id:
        logger.error("❌ Super Admin approval_group_id is not configured; refusing to send withdrawal notice to admin inbox")
        return False
    try:
        await safe_bot_send_message(
            bot,
            approval_group_id,
            message,
            parse_mode=parse_mode,
            **({"reply_markup": reply_markup} if reply_markup is not None else {})
        )
        logger.info(f"✅ Withdrawal notification sent ONLY to Super Admin approval group {approval_group_id}")
        return True
    except Exception as e:
        logger.error(f"Error sending withdrawal notification to approval group {approval_group_id}: {e}")
        return False

def update_bot_username(username: str) -> bool:
    return update_setting('bot_username', username)

def update_support_bot(support: str) -> bool:
    return update_setting('support_bot', support)

def update_admin_id(admin_id: str) -> bool:
    return update_setting('admin_id', admin_id)

def update_announce_channel(channel: str) -> bool:
    return update_setting('announce_channel', channel)

def update_referral_bonus(bonus: float) -> bool:
    return update_setting('referral_bonus', str(bonus))

def update_bot_name(name: str) -> bool:
    return update_setting('bot_name', name)

def update_daily_bonus(amount: float) -> bool:
    return update_setting('daily_bonus', str(amount))

def update_usd_to_kes_rate(rate: float) -> bool:
    return update_setting('usd_to_kes_rate', str(rate))

def get_admin_id_int() -> int:
    try:
        return int(get_admin_id())
    except (TypeError, ValueError):
        return 0


def init_database():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, data JSONB NOT NULL)")
    cur.execute("CREATE TABLE IF NOT EXISTS tasks (id BIGINT PRIMARY KEY, content TEXT NOT NULL, status TEXT NOT NULL, assigned_to TEXT, assigned_at BIGINT, created_by TEXT, approval_group_id BIGINT)")
    cur.execute("CREATE TABLE IF NOT EXISTS withdrawals (id SERIAL PRIMARY KEY, user_id TEXT NOT NULL, amount DECIMAL NOT NULL, final_amount DECIMAL NOT NULL, payment_method TEXT NOT NULL, address TEXT NOT NULL, status TEXT NOT NULL, created_at TIMESTAMP NOT NULL, mpesa_conversation_id TEXT, mpesa_transaction_id TEXT, mpesa_result TEXT, mpesa_result_code TEXT, mpesa_receipt TEXT, processed_at TIMESTAMP)")
    cur.execute("CREATE TABLE IF NOT EXISTS currencies (code TEXT PRIMARY KEY, name TEXT NOT NULL, symbol TEXT, rate_to_usd DECIMAL DEFAULT 1.0, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("CREATE TABLE IF NOT EXISTS user_activities (id SERIAL PRIMARY KEY, user_id TEXT NOT NULL, activity_type TEXT NOT NULL, description TEXT, details JSONB, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY, user_id TEXT NOT NULL, transaction_type TEXT NOT NULL, amount DECIMAL NOT NULL, balance_before DECIMAL NOT NULL, balance_after DECIMAL NOT NULL, description TEXT, reference_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("CREATE TABLE IF NOT EXISTS mpesa_callbacks (id SERIAL PRIMARY KEY, conversation_id TEXT, originator_conversation_id TEXT, result_code INTEGER, result_desc TEXT, raw_data JSONB, callback_data JSONB, received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("ALTER TABLE mpesa_callbacks ADD COLUMN IF NOT EXISTS conversation_id TEXT")
    cur.execute("ALTER TABLE mpesa_callbacks ADD COLUMN IF NOT EXISTS originator_conversation_id TEXT")
    cur.execute("ALTER TABLE mpesa_callbacks ADD COLUMN IF NOT EXISTS result_code INTEGER")
    cur.execute("ALTER TABLE mpesa_callbacks ADD COLUMN IF NOT EXISTS result_desc TEXT")
    cur.execute("ALTER TABLE mpesa_callbacks ADD COLUMN IF NOT EXISTS raw_data JSONB")
    cur.execute("ALTER TABLE mpesa_callbacks ADD COLUMN IF NOT EXISTS callback_data JSONB")
    cur.execute("CREATE TABLE IF NOT EXISTS mpesa_b2c_requests (id SERIAL PRIMARY KEY, conversation_id TEXT, originator_conversation_id TEXT, response_code TEXT, response_description TEXT, amount DECIMAL(10, 2), party_b TEXT, status TEXT DEFAULT 'pending', result_code TEXT, result_desc TEXT, transaction_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, command_id TEXT, request_data JSONB)")
    cur.execute("CREATE TABLE IF NOT EXISTS mpesa_unmatched_callbacks (id SERIAL PRIMARY KEY, callback_data JSONB NOT NULL, status TEXT NOT NULL, reason TEXT, conversation_id TEXT, originator_conversation_id TEXT, received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("CREATE TABLE IF NOT EXISTS mpesa_processed_callbacks (id SERIAL PRIMARY KEY, callback_id TEXT UNIQUE, conversation_id TEXT, transaction_id TEXT, status TEXT, processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("CREATE TABLE IF NOT EXISTS admin_settings (admin_id TEXT PRIMARY KEY, username TEXT, approval_group_id BIGINT, is_super_admin BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("SELECT COUNT(*) FROM currencies")
    count = cur.fetchone()[0]
    if count == 0:
        for code, name in CURRENCIES.items():
            try:
                cur.execute("INSERT INTO currencies (code, name, symbol, rate_to_usd) VALUES (%s, %s, %s, %s)", (code, name, code, 1.0))
            except Exception as e:
                print(f"Error inserting {code}: {e}")
        conn.commit()
    try:
        cur.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS mpesa_conversation_id TEXT")
        cur.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS mpesa_transaction_id TEXT")
        cur.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS mpesa_result TEXT")
        cur.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS mpesa_result_code TEXT")
        cur.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS mpesa_receipt TEXT")
        cur.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS processed_at TIMESTAMP")
        cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS created_by TEXT")
        cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS approval_group_id BIGINT")
        conn.commit()
    except Exception as e:
        print(f"Note: {e}")
    conn.commit()
    cur.close()
    conn.close()
    print("Database initialized - no hardcoded defaults")


def log_activity(user_id: str, activity_type: str, description: str = "", details: dict = None):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO user_activities (user_id, activity_type, description, details) VALUES (%s, %s, %s, %s)",
            (user_id, activity_type, description, Json(details) if details else None)
        )
        conn.commit()
    except Exception as e:
        print(f"Error logging activity for {user_id}: {e}")
    finally:
        cur.close()
        conn.close()

def log_transaction(user_id: str, transaction_type: str, amount: float, balance_before: float, balance_after: float, description: str = "", reference_id: str = ""):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO transactions 
               (user_id, transaction_type, amount, balance_before, balance_after, description, reference_id) 
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (user_id, transaction_type, amount, balance_before, balance_after, description, reference_id)
        )
        conn.commit()
    except Exception as e:
        print(f"Error logging transaction for {user_id}: {e}")
    finally:
        cur.close()
        conn.close()

def main_menu():
    return ReplyKeyboardMarkup(MAIN_MENU_BUTTONS, resize_keyboard=True)

def profile_menu():
    return ReplyKeyboardMarkup(
        [["📈 My Performance", "📦 Active Jobs"], ["📊 Activity Log", "🔙 Return"]],
        resize_keyboard=True
    )

def earnings_menu():
    return ReplyKeyboardMarkup(
        [["💸 Cash Out", "🧾 Payment Log"], ["🔙 Return"]],
        resize_keyboard=True
    )

def rewards_menu():
    return ReplyKeyboardMarkup(
        [["🔗 Invite Link", "👥 My Team"], ["🔙 Return"]],
        resize_keyboard=True
    )

def preferences_menu():
    return ReplyKeyboardMarkup(
        [["🌐 Timezone", "💱 Display Currency"], ["🔙 Return"]],
        resize_keyboard=True
    )

def support_menu():
    return ReplyKeyboardMarkup(
        [["🎧 Live Support", "❓ How It Works"], ["📢 Official Channel", "🔙 Return"]],
        resize_keyboard=True
    )

def currency_menu(currencies):
    keyboard = []
    for code, name in currencies:
        keyboard.append([f"{code} - {name}"])
    keyboard.append(["🔙 Return"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def timezone_continent_menu():
    keyboard = []
    continents = ["🌍 Africa", "🌎 America", "❄️ Antarctica", "🌏 Asia", "🌊 Atlantic", "🦘 Australia", "🇪🇺 Europe", "🌴 Indian", "🌺 Pacific", "🧊 Arctic", "🕒 UTC"]
    row = []
    for continent in continents:
        row.append(continent)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append(["🔙 Return"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def timezone_list_menu(continent):
    keyboard = []
    continent_map = {"🌍 Africa": "Africa", "🌎 America": "America", "❄️ Antarctica": "Antarctica", "🌏 Asia": "Asia", "🌊 Atlantic": "Atlantic", "🦘 Australia": "Australia", "🇪🇺 Europe": "Europe", "🌴 Indian": "Indian", "🌺 Pacific": "Pacific", "🧊 Arctic": "Arctic", "🕒 UTC": "UTC"}
    raw_continent = continent_map.get(continent, continent)
    timezones = ALL_TIMEZONES_WITH_OFFSETS.get(raw_continent, [])
    for tz in timezones:
        keyboard.append([tz])
    keyboard.append(["🔙 Return"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def format_timezone_simple(tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
        offset_sec = now.utcoffset().total_seconds()
        sign = "+" if offset_sec >= 0 else "-"
        offset_sec = abs(int(offset_sec))
        hours = offset_sec // 3600
        minutes = (offset_sec % 3600) // 60
        tz_display_name = tz_name.replace("_", " ")
        return f"{tz_display_name} GMT{sign}{hours:02d}:{minutes:02d}"
    except Exception:
        return f"{tz_name} GMT+00:00"

def get_utc_now():
    return datetime.now(ZoneInfo("UTC"))

def get_user_local_time(user):
    timezone = user.get("timezone", DEFAULT_TIMEZONE)
    return datetime.now(ZoneInfo(timezone))

def convert_db_to_user_time(db_dt, user):
    if isinstance(db_dt, str):
        db_dt = datetime.fromisoformat(db_dt.replace('Z', '+00:00'))
    elif isinstance(db_dt, (int, float)):
        db_dt = datetime.fromtimestamp(db_dt, ZoneInfo("UTC"))
    if db_dt.tzinfo is None:
        db_dt = db_dt.replace(tzinfo=ZoneInfo("UTC"))
    user_tz = user.get("timezone", DEFAULT_TIMEZONE)
    return db_dt.astimezone(ZoneInfo(user_tz))

def format_timestamp_with_timezone(timestamp, user, format_str="%Y-%m-%d %H:%M:%S"):
    local_dt = convert_db_to_user_time(timestamp, user)
    timezone_offset = get_timezone_offset(user.get("timezone", DEFAULT_TIMEZONE))
    return f"{local_dt.strftime(format_str)} ({timezone_offset})"

async def show_currency_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT code, name FROM currencies ORDER BY code;")
    currencies = cur.fetchall()
    if not currencies:
        await update.message.reply_text("❌ No currencies available.")
        cur.close()
        conn.close()
        return
    user['currencies_list'] = currencies
    user['selecting_currency'] = True
    cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), str(update.effective_user.id)))
    conn.commit()
    message_text = f"💰 *Currency*\n\nThe main currency in the bot is USD - US dollar, however, you can choose one of the {len(currencies)} currencies that will be used for visual display.\n\n- The currency you choose affects only the visual display, it can always be changed in settings\n\n"
    await update.message.reply_text(message_text, parse_mode="Markdown", reply_markup=currency_menu(currencies))
    cur.close()
    conn.close()

def get_mpesa_access_token():
    try:
        if not MPESA_CONSUMER_KEY or not MPESA_CONSUMER_SECRET:
            logger.error("M-Pesa consumer key/secret are not configured")
            return None
        auth_string = base64.b64encode(f"{MPESA_CONSUMER_KEY}:{MPESA_CONSUMER_SECRET}".encode()).decode()
        headers = {"Authorization": f"Basic {auth_string}"}
        
        with httpx.Client(timeout=30.0) as client:
            response = client.get(MPESA_AUTH_URL, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            return data.get("access_token")
        else:
            logger.error(f"M-Pesa auth failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"M-Pesa auth error: {e}")
        return None

def send_mpesa_b2c_direct(phone_number, amount_kes, conversation_id, user_id):
    try:
        if not MPESA_SECURITY_CREDENTIAL or not MPESA_INITIATOR_NAME or not MPESA_SHORTCODE:
            return {"error": "M-Pesa B2C credentials are incomplete"}
        if not MPESA_B2C_CALLBACK_BASE:
            return {"error": "MPESA_CALLBACK_BASE/CALLBACK_BASE is not configured"}

        access_token = get_mpesa_access_token()
        if not access_token:
            return {"error": "Failed to get M-Pesa access token"}
        
        amount_kes = int(amount_kes) if amount_kes else 0
        if amount_kes <= 0:
            return {"error": "Invalid amount - amount must be greater than 0"}
        
        phone_number = re.sub(r"\D", "", str(phone_number))
        if phone_number.startswith("0") and len(phone_number) == 10:
            phone_number = "254" + phone_number[1:]
        elif len(phone_number) == 9:
            phone_number = "254" + phone_number
        if not re.fullmatch(r"254\d{9}", phone_number):
            return {"error": "Invalid M-Pesa phone number. Use 2547XXXXXXXX or 2541XXXXXXXX"}
        
        payload = {
            "InitiatorName": MPESA_INITIATOR_NAME,
            "SecurityCredential": MPESA_SECURITY_CREDENTIAL,
            "CommandID": MPESA_COMMAND_ID,
            "Amount": str(amount_kes),
            "PartyA": MPESA_SHORTCODE,
            "PartyB": phone_number,
            "Remarks": f"Gmail Farming Withdrawal {conversation_id}",
            "QueueTimeOutURL": MPESA_TIMEOUT_URL,
            "ResultURL": MPESA_RESULT_URL,
            "Occasion": f"User_{user_id}"
        }
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        safe_payload = dict(payload); safe_payload["SecurityCredential"] = "***REDACTED***"
        logger.info(f"Sending M-Pesa B2C request: {json.dumps(safe_payload, default=str)}")
        
        with httpx.Client(timeout=30.0) as client:
            response = client.post(MPESA_B2C_URL, json=payload, headers=headers)
        
        logger.info(f"M-Pesa B2C response status: {response.status_code}")
        logger.info(f"M-Pesa B2C response body: {response.text}")
        
        if response.status_code == 200 or response.status_code == 201:
            return response.json()
        else:
            logger.error(f"M-Pesa B2C request failed: {response.status_code} - {response.text}")
            return {"error": f"Request failed: {response.status_code}", "details": response.text}
            
    except Exception as e:
        logger.error(f"M-Pesa B2C error: {e}")
        return {"error": str(e)}

def convert_usd_to_kes(usd_amount, rate=112.0):
    return usd_amount * 112.0  # Fixed: 1 USD = 112 KES

@app.route("/mpesa/result", methods=["POST"])
def mpesa_b2c_result():
    """Receive Safaricom B2C result callbacks and process them idempotently."""
    try:
        data = request.get_json(force=True, silent=False) or {}
        logger.info(f"M-Pesa B2C Result Callback Received: {json.dumps(data, default=str)[:2000]}")

        callback_data = extract_callback_data(data)
        callback_data['result_code'] = str(callback_data.get('result_code', '-1'))
        callback_id = generate_callback_id(callback_data)

        if is_callback_processed(callback_id):
            logger.info(f"Result callback {callback_id} already processed, skipping duplicate")
            return jsonify({"ResultCode": 0, "ResultDesc": "Already Processed"}), 200

        store_mpesa_callback(data)
        process_callback_async(callback_data, callback_id)
        mark_callback_processed(
            callback_id,
            callback_data.get('conversation_id'),
            callback_data.get('transaction_id'),
            callback_data.get('result_code', '-1')
        )
        return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"}), 200
    except Exception as e:
        logger.error(f"M-Pesa B2C result callback error: {e}")
        logger.error(traceback.format_exc())
        # Return 200 so Safaricom does not repeatedly hammer a malformed callback.
        return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"}), 200

@app.route("/mpesa/timeout", methods=["POST"])
def mpesa_b2c_timeout():
    try:
        data = request.get_json()
        logger.info(f"M-Pesa B2C Timeout Callback Received: {json.dumps(data, default=str)}")
        
        callback_data = extract_callback_data(data)
        callback_data['result_code'] = 'TIMEOUT'
        callback_data['result_desc'] = callback_data.get('result_desc', 'Timeout - No response from M-Pesa')
        callback_id = generate_callback_id(callback_data)
        
        if is_callback_processed(callback_id):
            logger.info(f"Timeout callback {callback_id} already processed, skipping duplicate")
            return jsonify({"ResultCode": 0, "ResultDesc": "Already Processed"})
        
        store_mpesa_callback(data)
        process_callback_async(callback_data, callback_id)
        mark_callback_processed(callback_id, callback_data.get('conversation_id'), callback_data.get('transaction_id'), 'TIMEOUT')
        
        logger.info(f"Timeout callback {callback_id} processed successfully")
        return jsonify({"ResultCode": 0, "ResultDesc": "Success"})
        
    except Exception as e:
        logger.error(f"M-Pesa B2C timeout callback error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"ResultCode": 1, "ResultDesc": "Error"})

def process_callback_async(callback_data, callback_id):
    try:
        try:
            current_loop = asyncio.get_running_loop()
            asyncio.create_task(process_callback_internal(callback_data, callback_id))
            logger.info("Callback processing task scheduled in current loop")
        except RuntimeError:
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                new_loop.run_until_complete(process_callback_internal(callback_data, callback_id))
                logger.info("Callback processed in new loop")
            finally:
                new_loop.close()
    except Exception as e:
        logger.error(f"Error processing callback: {e}")
        logger.error(traceback.format_exc())

async def process_callback_internal(callback_data, callback_id):
    try:
        bot = get_bot()
        if not bot:
            logger.error("Bot instance not available for callback processing")
            return
        
        result_code = str(callback_data.get('result_code', '-1'))
        conversation_id = callback_data.get('conversation_id')
        originator_conversation_id = callback_data.get('originator_conversation_id')
        transaction_id = callback_data.get('transaction_id')
        amount = callback_data.get('amount')
        result_desc = callback_data.get('result_desc', '')
        receiver_party = callback_data.get('receiver_party')
        
        logger.info(f"Processing callback - ConvID: {conversation_id}, ResultCode: {result_code}")
        
        withdrawal = find_pending_withdrawal(conversation_id, originator_conversation_id)
        
        if not withdrawal:
            log_unmatched_callback(callback_data, "success" if result_code == "0" else "failure", f"No pending withdrawal found for ConvID: {conversation_id}")
            logger.warning(f"No pending withdrawal found for conversation: {conversation_id}")
            return
        
        if withdrawal.get('status') != 'pending_mpesa':
            logger.info(f"Withdrawal #{withdrawal['id']} already in status: {withdrawal['status']}, skipping")
            return
        
        if result_code == "0":
            logger.info(f"Processing successful callback for withdrawal #{withdrawal['id']}")
            success = confirm_mpesa_withdrawal_withdrawal(withdrawal=withdrawal, transaction_id=transaction_id, amount=amount, result_desc=result_desc, receiver_party=receiver_party)
            if success:
                logger.info(f"✅ M-Pesa withdrawal confirmed: {withdrawal['id']}")
                await send_notifications_async(withdrawal, "success", transaction_id, amount, result_desc)
            else:
                logger.error(f"❌ Failed to confirm M-Pesa withdrawal: {withdrawal['id']}")
        else:
            failure_reason = result_desc or f"ResultCode: {result_code}"
            logger.info(f"Processing failed callback for withdrawal #{withdrawal['id']}, Reason: {failure_reason}")
            
            conn = get_conn()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cur.execute("SELECT status FROM withdrawals WHERE id = %s", (withdrawal['id'],))
                status_row = cur.fetchone()
                if status_row and status_row['status'] != 'pending_mpesa':
                    logger.info(f"⚠️ Withdrawal #{withdrawal['id']} already processed, skipping refund")
                    return
            finally:
                cur.close()
                conn.close()
            
            refund_success = refund_mpesa_withdrawal_withdrawal(withdrawal=withdrawal, failure_reason=failure_reason, result_code=result_code)
            
            if refund_success:
                logger.info(f"🔄 M-Pesa withdrawal refunded automatically: {withdrawal['id']}")
                await send_notifications_async(withdrawal, "failure", transaction_id, amount, failure_reason)
            else:
                logger.error(f"❌ Failed to refund M-Pesa withdrawal: {withdrawal['id']}")
                
    except Exception as e:
        logger.error(f"Error processing callback: {e}")
        logger.error(traceback.format_exc())

async def send_notifications_async(withdrawal, status, transaction_id, amount, result_desc):
    try:
        user_id = withdrawal['user_id']
        withdrawal_id = withdrawal['id']
        final_amount = float(withdrawal['final_amount'])
        phone = withdrawal['address']
        
        if status == "success":
            await send_mpesa_success_notification_async(user_id, final_amount, float(amount) if amount else 0, phone, transaction_id or "CONFIRMED")
            await send_admin_notification_async(withdrawal_id, user_id, final_amount, phone, transaction_id, "success")
        elif status == "failure":
            await send_mpesa_failure_notification_async(user_id, final_amount, f"Automatic Refund: {result_desc}", phone)
            await send_admin_notification_async(withdrawal_id, user_id, final_amount, phone, transaction_id, "failure", result_desc)
            
        logger.info(f"✅ Notifications sent for withdrawal #{withdrawal_id} (Status: {status})")
        
    except Exception as e:
        logger.error(f"Error sending notifications: {e}")
        logger.error(traceback.format_exc())

async def send_mpesa_success_notification_async(user_id, amount_usd, amount_kes, phone, transaction_id, fallback_amount_kes=None):
    try:
        try:
            resolved_amount_kes = float(amount_kes or fallback_amount_kes or 0)
        except (TypeError, ValueError):
            resolved_amount_kes = float(fallback_amount_kes or 0)

        message = f"""💰 *Withdrawal Successful!* 🎉

Your M-Pesa withdrawal has been processed successfully!

━━━━━━━━━━━━━━━━━━━━
📋 *Transaction Details:*
━━━━━━━━━━━━━━━━━━━━

💳 Amount: *${amount_usd:.2f}*
📱 Phone: *{phone}*
🆔 Transaction ID: *{transaction_id}*
📅 Date: *{format_eat_datetime()} EAT (GMT+3)*

━━━━━━━━━━━━━━━━━━━━

📌 Funds have been sent to your M-Pesa account.
💡 Check your M-Pesa messages for confirmation.

If you haven't received the money within 10 minutes,
please contact support. @{get_support_bot()}"""
        
        bot = get_bot()
        if bot:
            try:
                await bot.send_message(chat_id=int(user_id), text=message, parse_mode="Markdown")
                logger.info(f"✅ Success notification sent to user {user_id}")
            except Exception as e:
                logger.error(f"Error sending success notification to {user_id}: {e}")
        else:
            logger.error(f"❌ Bot not available for user {user_id}")
            
    except Exception as e:
        logger.error(f"Error sending success notification to {user_id}: {e}")

async def send_mpesa_failure_notification_async(user_id, amount_usd, failure_reason, phone):
    try:
        message = f"""❌ *Withdrawal Failed - Refunded*

Your M-Pesa withdrawal could not be processed.

━━━━━━━━━━━━━━━━━━━━
📋 *Transaction Details:*
━━━━━━━━━━━━━━━━━━━━

💰 Amount: *${amount_usd:.2f}* (✅ Refunded)
📱 Phone: *{phone}*
❌ Reason: *Unable to complete your request*

━━━━━━━━━━━━━━━━━━━━

💰 Your balance has been automatically refunded.

Need help? Contact @{get_support_bot()}"""
        
        bot = get_bot()
        if bot:
            try:
                await bot.send_message(chat_id=int(user_id), text=message, parse_mode="Markdown")
                logger.info(f"✅ Failure notification sent to user {user_id}")
            except Exception as e:
                logger.error(f"Error sending failure notification to {user_id}: {e}")
        else:
            logger.error(f"❌ Bot not available for user {user_id}")
            
    except Exception as e:
        logger.error(f"Error sending failure notification to {user_id}: {e}")

async def send_admin_notification_async(withdrawal_id, user_id, amount, phone, transaction_id, status, failure_reason=None):
    try:
        if status == "success":
            message = f"""✅ *M-Pesa Withdrawal SUCCESS*

━━━━━━━━━━━━━━━━━━━━
📋 *Transaction Details:*
━━━━━━━━━━━━━━━━━━━━

🆔 Withdrawal ID: `#{withdrawal_id}`
👤 User: `{user_id}`
💰 Amount: *${amount:.2f}*
📱 Phone: `{phone}`
🆔 Transaction: `{transaction_id or 'N/A'}`
📅 Time: `{format_eat_datetime()}`

✅ Status: COMPLETED"""
        elif status == "failure":
            message = f"""❌ *M-Pesa Withdrawal FAILED*

━━━━━━━━━━━━━━━━━━━━
📋 *Transaction Details:*
━━━━━━━━━━━━━━━━━━━━

🆔 Withdrawal ID: `#{withdrawal_id}`
👤 User: `{user_id}`
💰 Amount: *${amount:.2f}*
📱 Phone: `{phone}`
❌ Reason: `{failure_reason or 'Unknown'}`
📅 Time: `{format_eat_datetime()}`

🔄 Refund Status: ✅ AUTOMATICALLY REFUNDED"""
        
        bot = get_bot()
        if bot:
            try:
                approval_group_id = get_super_admin_approval_group()
                if not approval_group_id:
                    logger.error("❌ Super Admin approval_group_id is not configured; transaction result NOT sent to any admin inbox")
                    return
                await safe_bot_send_message(bot, approval_group_id, message, parse_mode="Markdown")
                logger.info(f"✅ Withdrawal transaction result sent ONLY to Super Admin approval group {approval_group_id}")
            except Exception as e:
                logger.error(f"Error sending admin notification: {e}")
        else:
            logger.error(f"❌ Bot not available for admin notification")
            
    except Exception as e:
        logger.error(f"Error sending admin notification: {e}")


async def safe_bot_send_message(bot, chat_id, text, **kwargs):
    """Send a Telegram message safely and never attempt an empty chat_id."""
    if not chat_id:
        logger.warning("Skipping Telegram message: empty chat_id")
        return False
    try:
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception as exc:
        msg = str(exc).lower()
        if "parse entities" in msg or "can't parse entities" in msg:
            kwargs.pop("parse_mode", None)
            kwargs.pop("entities", None)
            try:
                return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
            except Exception as retry_exc:
                logger.error("Telegram fallback send failed for %s: %s", chat_id, retry_exc)
                return False
        logger.error("Telegram send failed for %s: %s", chat_id, exc)
        return False


def get_eat_now():
    """Current East Africa Time (EAT), UTC/GMT+3."""
    return datetime.now(ZoneInfo("Africa/Nairobi"))


def format_eat_datetime(dt=None):
    """Format a timestamp in EAT (UTC/GMT+3)."""
    if dt is None:
        dt = get_eat_now()
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Africa/Nairobi"))
    else:
        dt = dt.astimezone(ZoneInfo("Africa/Nairobi"))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def get_bot():
    try:
        if application and hasattr(application, 'bot'):
            return application.bot
        return None
    except Exception as e:
        logger.error(f"Error getting bot instance: {e}")
        return None

def find_pending_withdrawal(conversation_id, originator_conversation_id):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        logger.info(f"Looking for pending withdrawal - ConvID: {conversation_id}, OrigConvID: {originator_conversation_id}")
        
        if conversation_id:
            cur.execute("""
                SELECT id, user_id, amount, final_amount, address, status, 
                       mpesa_conversation_id, mpesa_transaction_id
                FROM withdrawals 
                WHERE mpesa_conversation_id = %s AND status = 'pending_mpesa'
            """, (conversation_id,))
            withdrawal = cur.fetchone()
            if withdrawal:
                logger.info(f"✅ Found withdrawal by exact ConvID: {withdrawal['id']}")
                return withdrawal
        
        if originator_conversation_id:
            cur.execute("""
                SELECT id, user_id, amount, final_amount, address, status, 
                       mpesa_conversation_id, mpesa_transaction_id
                FROM withdrawals 
                WHERE mpesa_transaction_id = %s AND status = 'pending_mpesa'
            """, (originator_conversation_id,))
            withdrawal = cur.fetchone()
            if withdrawal:
                logger.info(f"✅ Found withdrawal by OriginatorConvID: {withdrawal['id']}")
                return withdrawal
        
        if conversation_id:
            search_pattern = f"%{conversation_id[:20]}%"
            cur.execute("""
                SELECT id, user_id, amount, final_amount, address, status, 
                       mpesa_conversation_id, mpesa_transaction_id
                FROM withdrawals 
                WHERE status = 'pending_mpesa' 
                AND mpesa_conversation_id LIKE %s
                ORDER BY created_at DESC LIMIT 1
            """, (search_pattern,))
            withdrawal = cur.fetchone()
            if withdrawal:
                logger.info(f"✅ Found withdrawal by partial ConvID: {withdrawal['id']}")
                return withdrawal
        
        logger.warning("❌ No pending withdrawal found with any strategy")
        return None
        
    except Exception as e:
        logger.error(f"Error finding pending withdrawal: {e}")
        logger.error(traceback.format_exc())
        return None
    finally:
        cur.close()
        conn.close()

def confirm_mpesa_withdrawal_withdrawal(withdrawal, transaction_id, amount, result_desc, receiver_party):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        withdrawal_id = withdrawal['id']
        user_id = withdrawal['user_id']
        
        logger.info(f"📝 Confirming withdrawal #{withdrawal_id} for user {user_id}")
        logger.info(f"   Transaction ID: {transaction_id}, Amount: {amount}, Receiver: {receiver_party}")
        
        cur.execute("""
            UPDATE withdrawals 
            SET status = 'processed', 
                mpesa_transaction_id = %s,
                mpesa_result = %s,
                processed_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'pending_mpesa'
        """, (transaction_id or "CONFIRMED", result_desc, withdrawal_id))
        
        if cur.rowcount == 0:
            logger.warning(f"⚠️ Withdrawal #{withdrawal_id} not in pending_mpesa status, skipping update")
            return True
        
        cur.execute("SELECT data FROM users WHERE user_id = %s", (user_id,))
        user_row = cur.fetchone()
        if user_row:
            user_data = user_row['data']
            withdrawal_amount = float(withdrawal['amount'])
            
            current_hold = user_data.get('withdrawal_onhold_balance', 0.0)
            user_data['withdrawal_onhold_balance'] = max(0, current_hold - withdrawal_amount)
            
            logger.info(f"   Removing ${withdrawal_amount:.2f} from on-hold balance (was: ${current_hold:.2f})")
            
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s", (Json(user_data), user_id))
            
            log_transaction(
                user_id,
                "withdrawal_approved",
                -float(withdrawal['final_amount']),
                float(user_data.get('available_balance', 0)),
                float(user_data.get('available_balance', 0)),
                f"M-Pesa withdrawal #{withdrawal_id} completed - Transaction: {transaction_id}",
                str(withdrawal_id)
            )
            
            log_activity(
                user_id,
                "withdrawal_approved",
                f"M-Pesa withdrawal #{withdrawal_id} completed - Transaction: {transaction_id}",
                {"amount": float(withdrawal['amount']), "final": float(withdrawal['final_amount'])}
            )
            
            logger.info(f"✅ Successfully confirmed withdrawal #{withdrawal_id}")
        
        conn.commit()
        return True
        
    except Exception as e:
        logger.error(f"Error confirming M-Pesa withdrawal: {e}")
        logger.error(traceback.format_exc())
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()

def refund_mpesa_withdrawal_withdrawal(withdrawal, failure_reason, result_code):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        withdrawal_id = withdrawal['id']
        user_id = withdrawal['user_id']
        withdrawal_amount = float(withdrawal['amount'])
        
        logger.info(f"🔄 Processing automatic refund for withdrawal #{withdrawal_id}")
        logger.info(f"   User: {user_id}, Amount: ${withdrawal_amount:.2f}")
        logger.info(f"   Reason: {failure_reason}, Result code: {result_code}")
        
        cur.execute("SELECT status FROM withdrawals WHERE id = %s", (withdrawal_id,))
        status_row = cur.fetchone()
        if status_row and status_row['status'] != 'pending_mpesa':
            logger.info(f"⚠️ Withdrawal #{withdrawal_id} already in status: {status_row['status']}, skipping refund")
            return True
        
        cur.execute("""
            UPDATE withdrawals 
            SET status = 'failed',
                mpesa_result = %s,
                mpesa_result_code = %s,
                processed_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'pending_mpesa'
        """, (failure_reason, result_code, withdrawal_id))
        
        if cur.rowcount == 0:
            logger.warning(f"⚠️ Withdrawal #{withdrawal_id} was already updated, skipping refund")
            return True
        
        cur.execute("SELECT data FROM users WHERE user_id = %s", (user_id,))
        user_row = cur.fetchone()
        
        if not user_row:
            logger.error(f"❌ User {user_id} not found for withdrawal #{withdrawal_id}")
            return False
        
        user_data = user_row['data']
        
        current_hold = user_data.get('withdrawal_onhold_balance', 0.0)
        current_available = user_data.get('available_balance', 0.0)
        
        logger.info(f"   Before refund - Available: ${current_available:.2f}, On-Hold: ${current_hold:.2f}")
        
        user_data['withdrawal_onhold_balance'] = max(0, current_hold - withdrawal_amount)
        user_data['available_balance'] = current_available + withdrawal_amount
        
        logger.info(f"   After refund - Available: ${user_data['available_balance']:.2f}, On-Hold: ${user_data['withdrawal_onhold_balance']:.2f}")
        
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s", (Json(user_data), user_id))
        
        log_transaction(
            user_id,
            "withdrawal_rejected",
            withdrawal_amount,
            current_available,
            user_data['available_balance'],
            f"🔄 M-Pesa withdrawal #{withdrawal_id} failed - Auto-refunded: {failure_reason}",
            str(withdrawal_id)
        )
        
        log_activity(
            user_id,
            "withdrawal_rejected",
            f"🔄 M-Pesa withdrawal #{withdrawal_id} failed - Auto-refunded: {failure_reason}",
            {"amount": withdrawal_amount, "reason": failure_reason}
        )
        
        conn.commit()
        logger.info(f"✅ Successfully refunded withdrawal #{withdrawal_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error refunding M-Pesa withdrawal: {e}")
        logger.error(traceback.format_exc())
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()

def log_unmatched_callback(callback_data, status, reason):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO mpesa_unmatched_callbacks 
            (callback_data, status, reason, conversation_id, originator_conversation_id) 
            VALUES (%s, %s, %s, %s, %s)
        """, (
            Json(callback_data), 
            status, 
            reason,
            callback_data.get('conversation_id'),
            callback_data.get('originator_conversation_id')
        ))
        conn.commit()
        logger.info(f"Unmatched callback logged: {reason}")
        
        asyncio.run_coroutine_threadsafe(
            send_admin_unmatched_notification_async(callback_data, status, reason),
            loop
        )
        
    except Exception as e:
        logger.error(f"Error logging unmatched callback: {e}")
    finally:
        cur.close()
        conn.close()

async def send_admin_unmatched_notification_async(callback_data, status, reason):
    try:
        conv_id = callback_data.get('conversation_id', 'N/A')
        result_code = callback_data.get('result_code', 'N/A')
        result_desc = callback_data.get('result_desc', 'N/A')
        
        message = f"""⚠️ *Unmatched M-Pesa Callback*

━━━━━━━━━━━━━━━━━━━━
📋 *Callback Details:*
━━━━━━━━━━━━━━━━━━━━

📊 Status: `{status}`
🆔 Conversation ID: `{conv_id}`
📝 Result Code: `{result_code}`
📄 Result Desc: `{result_desc}`
❌ Reason: `{reason}`

📅 Time: `{format_eat_datetime()}`

🔧 *Action Required:*
Check the mpesa_unmatched_callbacks table
Use /fixmpesa <id> refund or confirm

⚠️ Manual intervention may be required!"""
        
        bot = get_bot()
        if bot:
            approval_group_id = get_super_admin_approval_group()
            if not approval_group_id:
                logger.error("❌ Super Admin approval_group_id is not configured; unmatched callback not sent to admin inbox")
                return
            await safe_bot_send_message(bot, approval_group_id, message, parse_mode="Markdown")
            logger.info(f"✅ Unmatched callback sent ONLY to Super Admin approval group {approval_group_id}")
        else:
            logger.error("❌ Bot not available for admin notification")
            
    except Exception as e:
        logger.error(f"Error sending admin unmatched notification: {e}")

def store_mpesa_callback(data):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO mpesa_callbacks (callback_data) VALUES (%s)", (Json(data),))
        conn.commit()
        logger.info("M-Pesa callback stored successfully")
    except Exception as e:
        logger.error(f"Error storing M-Pesa callback: {e}")
    finally:
        cur.close()
        conn.close()

def store_b2c_request(data):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO mpesa_b2c_requests 
            (conversation_id, originator_conversation_id, response_code, response_description, 
             amount, party_b, command_id, request_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            data.get('conversation_id'),
            data.get('originator_conversation_id'),
            data.get('response_code'),
            data.get('response_description'),
            data.get('amount'),
            data.get('party_b'),
            data.get('command_id'),
            Json(data.get('request_data', {}))
        ))
        conn.commit()
        logger.info(f"B2C request stored successfully - ConvID: {data.get('conversation_id')}")
    except Exception as e:
        logger.error(f"Error storing B2C request: {e}")
    finally:
        cur.close()
        conn.close()

def is_callback_processed(callback_id: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM mpesa_processed_callbacks WHERE callback_id = %s", (callback_id,))
        return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking processed callback: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def mark_callback_processed(callback_id: str, conversation_id: str, transaction_id: str, status: str):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO mpesa_processed_callbacks 
            (callback_id, conversation_id, transaction_id, status) 
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (callback_id) DO UPDATE SET 
            status = EXCLUDED.status,
            processed_at = CURRENT_TIMESTAMP
        """, (callback_id, conversation_id, transaction_id, status))
        conn.commit()
    except Exception as e:
        logger.error(f"Error marking callback processed: {e}")
    finally:
        cur.close()
        conn.close()

def extract_callback_data(data):
    try:
        result = data.get('Result', {})
        return {
            'conversation_id': result.get('ConversationID', data.get('ConversationID')),
            'originator_conversation_id': result.get('OriginatorConversationID', data.get('OriginatorConversationID')),
            'transaction_id': result.get('TransactionID', data.get('TransactionID')),
            'result_code': result.get('ResultCode', data.get('ResultCode', '-1')),
            'result_desc': result.get('ResultDesc', data.get('ResultDesc', '')),
            'amount': result.get('Amount', data.get('Amount')),
            'receiver_party': result.get('ReceiverParty', data.get('ReceiverParty'))
        }
    except Exception as e:
        logger.error(f"Error extracting callback data: {e}")
        return {
            'conversation_id': data.get('ConversationID'),
            'originator_conversation_id': data.get('OriginatorConversationID'),
            'transaction_id': data.get('TransactionID'),
            'result_code': data.get('ResultCode', '-1'),
            'result_desc': data.get('ResultDesc', ''),
            'amount': data.get('Amount'),
            'receiver_party': data.get('ReceiverParty')
        }

def generate_callback_id(callback_data):
    conv_id = callback_data.get('conversation_id', '')
    txn_id = callback_data.get('transaction_id', '')
    result_code = callback_data.get('result_code', '')
    data_str = f"{conv_id}:{txn_id}:{result_code}"
    return hashlib.md5(data_str.encode()).hexdigest()

def confirm_mpesa_withdrawal(conversation_id, transaction_id, amount, result_desc, receiver_party):
    withdrawal = find_pending_withdrawal(conversation_id, None)
    if withdrawal:
        return confirm_mpesa_withdrawal_withdrawal(withdrawal, transaction_id, amount, result_desc, receiver_party)
    return False

def refund_mpesa_withdrawal(conversation_id, failure_reason, result_code):
    withdrawal = find_pending_withdrawal(conversation_id, None)
    if withdrawal:
        return refund_mpesa_withdrawal_withdrawal(withdrawal, failure_reason, result_code)
    return False

async def release_affiliate_bonus(referrer_id: str, referred_user_id: str, context: ContextTypes.DEFAULT_TYPE):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute("SELECT data FROM users WHERE user_id = %s;", (referrer_id,))
        referrer_row = cur.fetchone()
        if not referrer_row:
            return
        
        referrer_data = referrer_row["data"]
        referral_bonus = get_referral_bonus()
        
        referred_users_completed = referrer_data.get("referred_users_completed", [])
        if referred_user_id in referred_users_completed:
            return
        
        affiliate_onhold = referrer_data.get("affiliate_onhold_balance", 0.0)
        
        if affiliate_onhold >= referral_bonus:
            balance_before = referrer_data.get("available_balance", 0.0)
            referrer_data["affiliate_onhold_balance"] = affiliate_onhold - referral_bonus
            referrer_data["available_balance"] = balance_before + referral_bonus
            
            if "referred_users_completed" not in referrer_data:
                referrer_data["referred_users_completed"] = []
            referrer_data["referred_users_completed"].append(referred_user_id)
            
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(referrer_data), referrer_id))
            conn.commit()
            
            log_transaction(
                referrer_id,
                "affiliate_bonus_release",
                referral_bonus,
                balance_before,
                referrer_data["available_balance"],
                f"Affiliate bonus released for referral {referred_user_id}",
                referred_user_id
            )
            
            bot_name = get_bot_name()
            await context.bot.send_message(
                chat_id=int(referrer_id),
                text=f"🎉 *Affiliate Bonus Released!*\n\n"
                     f"Your referred user has completed their first task! 🎯\n"
                     f"💰 *${referral_bonus:.2f}* has been moved from affiliate on-hold to your main balance.\n\n"
                     f"Keep inviting more friends to earn more with {bot_name}!",
                parse_mode="Markdown"
            )
            
            log_activity(
                referrer_id,
                "affiliate_bonus_released",
                f"Affiliate bonus ${referral_bonus:.2f} released for referral {referred_user_id}",
                {"amount": referral_bonus, "referred_user": referred_user_id}
            )
            
            print(f"✅ Released affiliate bonus ${referral_bonus:.2f} to {referrer_id} for referral {referred_user_id}")
    except Exception as e:
        print(f"Error releasing affiliate bonus: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = str(update.effective_user.id)
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT COUNT(*) as c FROM admin_settings;")
        admin_count_row = cur.fetchone()
        admin_count = admin_count_row['c'] if admin_count_row else 0
        is_first_user = (admin_count == 0)
        if is_first_user:
            username = update.effective_user.username or f"user_{uid}"
            cur.execute("INSERT INTO admin_settings (admin_id, username, approval_group_id, is_super_admin) VALUES (%s, %s, NULL, TRUE) ON CONFLICT (admin_id) DO UPDATE SET is_super_admin = TRUE, updated_at = CURRENT_TIMESTAMP", (uid, username))
            conn.commit()
            print(f"First user {uid} set as super admin")
            cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
            existing_user = cur.fetchone()
            user_data = DEFAULT_USER.copy() if 'DEFAULT_USER' in globals() else {}
            user_data['is_super_admin'] = True
            user_data['setup_step'] = 0
            user_data['setup_in_progress'] = True
            if existing_user:
                cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user_data), uid))
            else:
                cur.execute("INSERT INTO users (user_id, data) VALUES (%s, %s);", (uid, Json(user_data)))
            conn.commit()
            cur.close()
            conn.close()
            setup_msg = "Welcome! You are first user - you are now SUPER ADMIN!\n\nBot Setup Required\nLet's configure your bot. I'll ask 6 settings one by one:\n1. Approval Group ID\n2. Task Pay\n3. Minimum Withdrawal\n4. Bot Username\n5. Announcement Channel\n6. Support Bot\n\nFirst: Send me your approval group ID"
            await update.message.reply_text(setup_msg)
            return
        cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
        existing_user = cur.fetchone()
        if existing_user and existing_user["data"].get("banned", False):
            await update.message.reply_text("Account Banned")
            cur.close()
            conn.close()
            return
        if existing_user and existing_user["data"].get("setup_in_progress"):
            step = existing_user["data"].get("setup_step", 0)
            if step < len(SETUP_KEYS_ORDER):
                key, desc = SETUP_KEYS_ORDER[step]
                cur.close()
                conn.close()
                await update.message.reply_text(f"Setup in progress ({step+1}/{len(SETUP_KEYS_ORDER)}) Please send: {desc} Current key: {key}")
                return
        task_pay = get_task_pay()
        withdrawal_limit = get_withdrawal_limit()
        bot_username = get_bot_username()
        support_bot = get_support_bot()
        referral_bonus = get_referral_bonus()
        bot_name = get_bot_name()
        daily_bonus = get_daily_bonus()
        if not existing_user:
            user_data = DEFAULT_USER.copy()
            if context.args:
                ref = context.args[0]
                if ref != uid:
                    cur.execute("SELECT user_id, data FROM users WHERE user_id = %s;", (ref,))
                    ref_user = cur.fetchone()
                    if ref_user:
                        ref_data = ref_user["data"]
                        user_data["referred_by"] = ref
                        ref_data.setdefault("referrals", [])
                        if uid not in ref_data["referrals"]:
                            ref_data["referrals"].append(uid)
                            ref_data["affiliate_onhold_balance"] = ref_data.get("affiliate_onhold_balance", 0.0) + referral_bonus
                            ref_data["referral_bonus_pending"] = True
                            log_activity(ref, "referral_pending", f"New referral: {uid}", {"referral_id": uid, "bonus": referral_bonus})
                            try:
                                await context.bot.send_message(chat_id=int(ref), text=f"New Referral! {uid} joined. ${referral_bonus:.2f} on-hold.")
                            except:
                                pass
                            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(ref_data), ref))
            cur.execute("INSERT INTO users (user_id, data) VALUES (%s, %s);", (uid, Json(user_data)))
            log_activity(uid, "registration", "User registered", {"referred_by": user_data.get("referred_by")})
            conn.commit()
        cur.close()
        conn.close()
        if is_admin(uid) and not is_setup_completed():
            await update.message.reply_text(f"Setup Incomplete Use /setup Welcome to {bot_name}! Payment: ${task_pay:.2f}, Min: ${withdrawal_limit:.2f}", reply_markup=main_menu())
            return
        welcome_msg = f"""{WELCOME_MESSAGE}

💰 *Current Rates:*
┣━━ Per Task: ${task_pay:.2f} to $0.25
┣━━ Min Withdraw: ${withdrawal_limit:.2f}
┣━━ Daily Bonus: ${daily_bonus:.2f}
┗━━ Referral Bonus: ${referral_bonus:.2f}

📞 Support: @{support_bot}
🤖 Bot: @{bot_username}

👇 Tap below for registration guide!"""

        # Inline button under welcome text, not in main buttons
        welcome_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 How To Create?", callback_data="howto")]
        ])
        await update.message.reply_text(welcome_msg, reply_markup=welcome_keyboard)
        # Then send main menu as separate message
        await update.message.reply_text("👇 Main Menu:", reply_markup=main_menu())
    except Exception as e:
        print("DB ERROR:", e, traceback.format_exc())
        await update.message.reply_text("Database error.")


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized - You are not an admin.")
        return
    
    # Get full text after command - preserves newlines
    full_text = update.message.text or ""
    # Remove command prefix /addtask, /add_task, /addtask@botname
    task_content = ""
    if full_text.startswith("/"):
        # Split first line at space, rest is content
        parts = full_text.split(None, 1)  # split on first whitespace
        if len(parts) > 1:
            task_content = parts[1].strip()
    
    # If no content in same message, switch to interactive mode
    if not task_content:
        await update.message.reply_text(
            "📝 *Add Task - Interactive Mode*\n\n"
            "Please send the task details in this format:\n\n"
            "```\n"
            "First name: John\n"
            "Last name: ✖️\n"
            "Email: example@gmail.com\n"
            "Password: Abc123\n"
            "```\n\n"
            "Or send /cancel to cancel.",
            parse_mode="Markdown"
        )
        # Set awaiting flag
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute("SELECT data FROM users WHERE user_id = %s;", (admin_id,))
            row = cur.fetchone()
            user = row["data"] if row else DEFAULT_USER.copy()
            user["awaiting_new_task"] = True
            user["task_creator_admin"] = admin_id
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), admin_id))
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return
    
    # Validate format - must have at least 3 lines or contain Email and Password
    if task_content.count("\n") < 2 and "Email:" not in task_content and "email" not in task_content.lower():
        await update.message.reply_text(
            "❌ Invalid task format.\n\n"
            "Expected format:\n"
            "First name: John\nLast name: ✖️\nEmail: example@gmail.com\nPassword: Abc123\n\n"
            "Use /addtask_interactive for step-by-step."
        )
        return
    
    task_id = int(time.time() * 1000) + random.randint(1, 1000)
    approval_group_id = get_admin_approval_group(admin_id)
    
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute(
            "INSERT INTO tasks (id, content, status, created_by, approval_group_id) VALUES (%s, %s, %s, %s, %s);",
            (task_id, task_content, "available", admin_id, approval_group_id)
        )
        conn.commit()
        
        log_activity(admin_id, "task_added", f"Task {task_id} added by admin {admin_id}", {"task_id": task_id})
        
        await update.message.reply_text(
            f"✅ Task added successfully!\n\n"
            f"🆔 Task ID: `{task_id}`\n"
            f"📋 Status: Available\n"
            f"👤 Created by: Admin {admin_id}\n"
            f"📢 Approval Group: {approval_group_id}",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error adding task: {str(e)}")
    finally:
        cur.close()
        conn.close()


async def add_task_interactive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized - You are not an admin.")
        return
    
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute("SELECT data FROM users WHERE user_id = %s;", (admin_id,))
        row = cur.fetchone()
        
        if row:
            user = row["data"]
        else:
            user = DEFAULT_USER.copy()
        
        user["awaiting_new_task"] = True
        user["task_creator_admin"] = admin_id
        
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), admin_id))
        conn.commit()
        
        await update.message.reply_text(
            "📝 *Add New Task - Interactive Mode*\n\n"
            "Please send the task details in this format:\n\n"
            "```\n"
            "First name: John\n"
            "Last name: ✖️\n"
            "Email: example@gmail.com\n"
            "Password: Abc123\n"
            "```\n\n"
            "Or send /cancel to cancel.",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    finally:
        cur.close()
        conn.close()

async def delete_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized - You are not an admin.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /deletetask <task_id>\n\nExample: /deletetask 123456789")
        return
    
    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid task ID. Please provide a number.")
        return
    
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        is_super = is_super_admin(admin_id)
        
        if is_super:
            cur.execute("SELECT * FROM tasks WHERE id = %s;", (task_id,))
        else:
            cur.execute("SELECT * FROM tasks WHERE id = %s AND created_by = %s;", (task_id, admin_id))
        
        task = cur.fetchone()
        
        if not task:
            await update.message.reply_text(
                f"❌ Task with ID {task_id} not found or you don't have permission to delete it.\n\n"
                f"Note: You can only delete tasks that you created."
            )
            cur.close()
            conn.close()
            return
        
        if task["assigned_to"] and task["status"] == "assigned":
            uid = task["assigned_to"]
            cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
            user_row = cur.fetchone()
            if user_row:
                user_data = user_row["data"]
                if user_data.get("active_task_id") == task_id:
                    user_data["active_task_id"] = None
                    cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user_data), uid))
        
        cur.execute("DELETE FROM tasks WHERE id = %s;", (task_id,))
        conn.commit()
        
        log_activity(admin_id, "task_deleted", f"Task {task_id} deleted by admin {admin_id}", {"task_id": task_id})
        
        await update.message.reply_text(f"✅ Task {task_id} deleted successfully.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error deleting task: {str(e)}")
    finally:
        cur.close()
        conn.close()

async def admin_tasks_overview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized - You are not an admin.")
        return
    
    is_super = is_super_admin(admin_id)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if is_super:
            cur.execute("SELECT COUNT(*) as total, COUNT(CASE WHEN status = 'available' THEN 1 END) as available, COUNT(CASE WHEN status = 'assigned' THEN 1 END) as assigned, COUNT(CASE WHEN status = 'submitted' THEN 1 END) as submitted, COUNT(CASE WHEN status = 'approved' THEN 1 END) as approved, COUNT(CASE WHEN status = 'rejected' THEN 1 END) as rejected, COUNT(DISTINCT created_by) as admin_count FROM tasks;")
        else:
            cur.execute("SELECT COUNT(*) as total, COUNT(CASE WHEN status = 'available' THEN 1 END) as available, COUNT(CASE WHEN status = 'assigned' THEN 1 END) as assigned, COUNT(CASE WHEN status = 'submitted' THEN 1 END) as submitted, COUNT(CASE WHEN status = 'approved' THEN 1 END) as approved, COUNT(CASE WHEN status = 'rejected' THEN 1 END) as rejected, COUNT(DISTINCT created_by) as admin_count FROM tasks WHERE created_by = %s;", (admin_id,))
        
        stats = cur.fetchone()
        
        if is_super:
            cur.execute("SELECT created_by, COUNT(*) as task_count, COUNT(CASE WHEN status = 'available' THEN 1 END) as available FROM tasks GROUP BY created_by;")
            admin_stats = cur.fetchall()
            
            msg = f"📊 *All Tasks Overview*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        else:
            msg = f"📊 *Your Tasks Overview*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        
        msg += f"📂 Total Tasks: *{stats['total']}*\n"
        msg += f"📥 Available: *{stats['available']}*\n"
        msg += f"🟡 Assigned: *{stats['assigned']}*\n"
        msg += f"🟠 Pending Review: *{stats['submitted']}*\n"
        msg += f"✅ Approved: *{stats['approved']}*\n"
        msg += f"❌ Rejected: *{stats['rejected']}*\n"
        msg += f"👤 Admins with Tasks: *{stats['admin_count']}*\n\n"
        
        if is_super and admin_stats:
            msg += "━━━━━━━━━━━━━━━━━━━━\n"
            msg += "*📋 Tasks by Admin:*\n"
            msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
            for stat in admin_stats:
                admin_username = get_admin_username(stat['created_by']) or stat['created_by']
                msg += f"👤 Admin `{admin_username}`\n"
                msg += f"┣━━ Total: *{stat['task_count']}*\n"
                msg += f"┗━━ Available: *{stat['available']}*\n\n"
        
        if is_super:
            msg += "━━━━━━━━━━━━━━━━━━━━\n"
            msg += "*💡 Commands:*\n"
            msg += "• `/addtask` - Add a new task\n"
            msg += "• `/viewtasks` - View all tasks\n"
            msg += "• `/deletetask <id>` - Delete a task\n"
            msg += "• `/admins` - Manage admins"
        else:
            msg += "━━━━━━━━━━━━━━━━━━━━\n"
            msg += "*💡 Commands:*\n"
            msg += "• `/addtask_interactive` - Add a new task\n"
            msg += "• `/viewtasks` - View your tasks\n"
            msg += "• `/deletetask <id>` - Delete a task"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    finally:
        cur.close()
        conn.close()



def build_admin_task_dashboard(tasks, available_count, assigned_count, page=0, total_pages=1):
    """Render the admin task dashboard grouped by pool/in-progress status."""
    available = [t for t in tasks if t.get('status') == 'available']
    assigned = [t for t in tasks if t.get('status') == 'assigned']

    msg = "🛠 *ADMIN TASK DASHBOARD*\n\n"
    msg += "━━━━━━━━━━━━━━\n\n"
    msg += f"📥 *Available (Pool)* ({available_count})\n\n"
    msg += "━━━━━━━━━━━━━━\n\n"

    if available:
        for i, task in enumerate(available, 1):
            assigned_to = task.get('assigned_to') or '—'
            content = task.get('content') or ''
            msg += f"🔢 *Task #{i}*\n\n"
            msg += f"🆔 ID: `{task['id']}`\n\n"
            msg += f"👤 Assigned: `{assigned_to}`\n\n"
            msg += "📄 Content:\n\n"
            msg += f"```\n{content}\n```\n\n"
    else:
        msg += "No available tasks.\n\n"

    msg += f"🟠 *Assigned (In Progress)* ({assigned_count})\n\n"
    msg += "━━━━━━━━━━━━━━\n\n"

    if assigned:
        for i, task in enumerate(assigned, 1):
            assigned_to = task.get('assigned_to') or '—'
            content = task.get('content') or ''
            msg += f"🔢 *Task #{i}*\n\n"
            msg += f"🆔 ID: `{task['id']}`\n\n"
            msg += f"👤 Assigned: `{assigned_to}`\n\n"
            msg += "📄 Content:\n\n"
            msg += f"```\n{content}\n```\n\n"
    else:
        msg += "No tasks currently in progress.\n\n"

    return msg.strip()

async def view_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)

    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized - You are not an admin.")
        return

    is_super = is_super_admin(admin_id)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:
        if is_super:
            cur.execute("""
                SELECT id, content, status, assigned_to, created_by
                FROM tasks
                ORDER BY CASE WHEN status = 'available' THEN 0 ELSE 1 END,
                         id DESC
            """)
        else:
            cur.execute("""
                SELECT id, content, status, assigned_to, created_by
                FROM tasks
                WHERE created_by = %s
                ORDER BY CASE WHEN status = 'available' THEN 0 ELSE 1 END,
                         id DESC
            """, (admin_id,))

        tasks = cur.fetchall()
        if not tasks:
            await update.message.reply_text("📭 No tasks found.")
            return

        creator_names = {}
        for t in tasks:
            creator_id = str(t.get("created_by") or "")
            if creator_id and creator_id not in creator_names:
                try:
                    creator_names[creator_id] = get_admin_username(creator_id) or creator_id
                except Exception:
                    creator_names[creator_id] = creator_id

        available = [t for t in tasks if t["status"] == "available"]
        assigned = [t for t in tasks if t["status"] == "assigned"]
        other = [t for t in tasks if t["status"] not in ("available", "assigned")]

        lines = ["🛠 *ADMIN TASK DASHBOARD*", "━━━━━━━━━━━━━━", ""]

        def add_section(title, emoji, items):
            lines.append(f"{emoji} *{title}* ({len(items)})")
            lines.append("━━━━━━━━━━━━━━")
            for idx, task in enumerate(items, 1):
                creator = creator_names.get(str(task.get("created_by") or ""), "—")
                assigned_to = str(task.get("assigned_to") or "—")
                content = (task.get("content") or "").strip().replace("```", "'''")
                lines.extend([
                    f"🔢 *Task #{idx}*",
                    f"🆔 ID: `{task['id']}`",
                    f"👤 Posted by: `{creator}`",
                    f"👤 Assigned: `{assigned_to}`",
                    "📄 Content:",
                    "```",
                    content,
                    "```",
                    ""
                ])

        add_section("Available (Pool)", "📥", available)
        add_section("Assigned (In Progress)", "🟠", assigned)
        if other:
            add_section("Other", "⚪", other)

        msg = "\n".join(lines).rstrip()
        MAX_LEN = 3800
        if len(msg) <= MAX_LEN:
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            current = ""
            for block in msg.split("\n\n"):
                candidate = f"{current}\n\n{block}" if current else block
                if len(candidate) > MAX_LEN and current:
                    await update.message.reply_text(current, parse_mode="Markdown")
                    current = block
                else:
                    current = candidate
            if current:
                await update.message.reply_text(current, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error in view_tasks: {e}")
        await update.message.reply_text("❌ Error loading task dashboard.")
    finally:
        cur.close()
        conn.close()

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    
    if not is_super_admin(admin_id):
        await update.message.reply_text("❌ Only the Super Admin can add new admins.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /addadmin <user_id> <username>\n\n"
            "Example: /addadmin 123456789 JohnDoe\n\n"
            "To set the approval group:\n"
            "/setapprovalgroup <user_id> <group_id>"
        )
        return
    
    new_admin_id = context.args[0]
    username = " ".join(context.args[1:])
    
    if not new_admin_id.isdigit():
        await update.message.reply_text("❌ Invalid user ID. Must be a number.")
        return
    
    if is_admin(new_admin_id):
        await update.message.reply_text(f"⚠️ User `{new_admin_id}` is already an admin.", parse_mode="Markdown")
        return
    
    conn = get_conn()
    cur = conn.cursor()
    
    try:
        cur.execute(
            "INSERT INTO admin_settings (admin_id, username, approval_group_id, is_super_admin) VALUES (%s, %s, %s, FALSE);",
            (new_admin_id, username, DEFAULT_APPROVAL_GROUP_ID)
        )
        conn.commit()
        
        await update.message.reply_text(
            f"✅ Admin added successfully!\n\n"
            f"👤 User ID: `{new_admin_id}`\n"
            f"📛 Username: `{username}`\n"
            f"📢 Approval Group: `{DEFAULT_APPROVAL_GROUP_ID}`\n\n"
            f"To change the approval group, use:\n"
            f"/setapprovalgroup {new_admin_id} <group_id>",
            parse_mode="Markdown"
        )
        
        try:
            await context.bot.send_message(
                chat_id=int(new_admin_id),
                text=f"🎉 *You have been added as an admin!*\n\n"
                     f"📛 Username: {username}\n"
                     f"📢 Approval Group: {DEFAULT_APPROVAL_GROUP_ID}\n\n"
                     f"Use /dashboard to access the admin panel.\n"
                     f"Use /addtask_interactive to add tasks.\n"
                     f"Use /viewtasks to view your tasks.",
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Could not notify new admin {new_admin_id}: {e}")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    finally:
        cur.close()
        conn.close()

async def set_other_admin_approval_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Super Admin only: set approval group for another normal admin."""
    super_admin_id = str(update.effective_user.id)

    if not is_super_admin(super_admin_id):
        await update.message.reply_text("❌ Only the Super Admin can set another admin's approval group.")
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: /setadminapprovalgroup <admin_user_id> <approval_group_id>\n\n"
            "Example:\n"
            "/setadminapprovalgroup 6828545608 -1001234567890"
        )
        return

    target_admin_id = context.args[0].strip()
    group_id_raw = context.args[1].strip()

    if not target_admin_id.isdigit():
        await update.message.reply_text("❌ Invalid admin user ID. It must be numeric.")
        return

    try:
        approval_group_id = int(group_id_raw)
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid approval group ID. Use the Telegram numeric group ID, "
            "e.g. `-1001234567890`.",
            parse_mode="Markdown"
        )
        return

    if target_admin_id == super_admin_id:
        await update.message.reply_text("❌ This command is only for another normal admin.")
        return

    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT admin_id, username, is_super_admin FROM admin_settings WHERE admin_id = %s;",
            (target_admin_id,)
        )
        admin = cur.fetchone()

        if not admin:
            await update.message.reply_text(
                f"❌ Admin `{target_admin_id}` was not found.",
                parse_mode="Markdown"
            )
            return

        if admin["is_super_admin"]:
            await update.message.reply_text("❌ This command cannot modify the Super Admin.")
            return

        cur.execute(
            "UPDATE admin_settings "
            "SET approval_group_id = %s, updated_at = CURRENT_TIMESTAMP "
            "WHERE admin_id = %s AND is_super_admin = FALSE;",
            (approval_group_id, target_admin_id)
        )
        conn.commit()

        username = admin["username"] or target_admin_id
        await update.message.reply_text(
            "✅ *Admin Approval Group Updated!*\n\n"
            f"👤 Admin: `{username}`\n"
            f"🆔 User ID: `{target_admin_id}`\n"
            f"📢 Approval Group ID: `{approval_group_id}`",
            parse_mode="Markdown"
        )
    except Exception as e:
        conn.rollback()
        logger.error(f"Error setting approval group for admin {target_admin_id}: {e}")
        await update.message.reply_text(f"❌ Error updating approval group: {e}")
    finally:
        cur.close()
        conn.close()


async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    
    if not is_super_admin(admin_id):
        await update.message.reply_text("❌ Only the Super Admin can remove admins.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /removeadmin <user_id>\n\nExample: /removeadmin 123456789")
        return
    
    target_admin_id = context.args[0]
    
    if not target_admin_id.isdigit():
        await update.message.reply_text("❌ Invalid user ID. Must be a number.")
        return
    
    if target_admin_id == admin_id:
        await update.message.reply_text("❌ You cannot remove yourself as Super Admin.")
        return
    
    if not is_admin(target_admin_id):
        await update.message.reply_text(f"⚠️ User `{target_admin_id}` is not an admin.", parse_mode="Markdown")
        return
    
    conn = get_conn()
    cur = conn.cursor()
    
    try:
        cur.execute("DELETE FROM admin_settings WHERE admin_id = %s;", (target_admin_id,))
        conn.commit()
        
        await update.message.reply_text(
            f"✅ Admin `{target_admin_id}` removed successfully.",
            parse_mode="Markdown"
        )
        
        try:
            await context.bot.send_message(
                chat_id=int(target_admin_id),
                text="⚠️ *You have been removed as an admin.*\n\n"
                     "You no longer have admin privileges in this bot.",
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Could not notify removed admin {target_admin_id}: {e}")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    finally:
        cur.close()
        conn.close()

async def set_approval_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    
    if not is_super_admin(admin_id):
        await update.message.reply_text("❌ Only the Super Admin can change approval groups.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /setapprovalgroup <admin_id> <group_id>\n\n"
            "Example: /setapprovalgroup 123456789 -100123456789\n\n"
            "To set your own approval group:\n"
            "/setmyapprovalgroup <group_id>"
        )
        return
    
    target_admin_id = context.args[0]
    try:
        group_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid group ID. Must be a number.")
        return
    
    if not is_admin(target_admin_id):
        await update.message.reply_text(f"⚠️ User `{target_admin_id}` is not an admin.", parse_mode="Markdown")
        return
    
    if set_admin_approval_group(target_admin_id, group_id):
        await update.message.reply_text(
            f"✅ Approval group updated for admin `{target_admin_id}`!\n\n"
            f"📢 New Approval Group: `{group_id}`\n\n"
            f"All task submissions from this admin's tasks will now be sent to this group.",
            parse_mode="Markdown"
        )
        
        try:
            admin_username = get_admin_username(target_admin_id)
            await context.bot.send_message(
                chat_id=int(target_admin_id),
                text=f"🔄 *Approval Group Updated*\n\n"
                     f"Your approval group has been changed to:\n"
                     f"📢 `{group_id}`\n\n"
                     f"All task submissions from your tasks will now be sent to this group.\n\n"
                     f"If this is incorrect, contact the Super Admin.",
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Could not notify admin {target_admin_id}: {e}")
    else:
        await update.message.reply_text("❌ Failed to update approval group.")

async def set_my_approval_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    
    if not is_admin(admin_id):
        await update.message.reply_text("❌ You are not an admin.")
        return
    
    if is_super_admin(admin_id):
        await update.message.reply_text("❌ Super Admin should use /setapprovalgroup to manage other admins.\n\nOr use /setmyapprovalgroup for your own.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /setmyapprovalgroup <group_id>\n\nExample: /setmyapprovalgroup -100123456789")
        return
    
    try:
        group_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid group ID. Must be a number.")
        return
    
    if set_admin_approval_group(admin_id, group_id):
        await update.message.reply_text(
            f"✅ Your approval group has been updated!\n\n"
            f"📢 New Approval Group: `{group_id}`\n\n"
            f"All task submissions from your tasks will now be sent to this group.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ Failed to update approval group.")

async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    
    if not is_admin(admin_id):
        await update.message.reply_text("❌ You are not an admin.")
        return
    
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute("SELECT admin_id, username, approval_group_id, is_super_admin, created_at FROM admin_settings ORDER BY is_super_admin DESC, created_at;")
        admins = cur.fetchall()
        
        if not admins:
            await update.message.reply_text("No admins found.")
            return
        
        msg = "👥 *Admin List*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for admin in admins:
            status = "⭐ SUPER ADMIN" if admin['is_super_admin'] else "👤 Admin"
            username = admin['username'] or admin['admin_id']
            msg += f"🆔 ID: `{admin['admin_id']}`\n"
            msg += f"📛 Username: `{username}`\n"
            msg += f"📢 Approval Group: `{admin['approval_group_id']}`\n"
            msg += f"🔰 Status: {status}\n"
            msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        if is_super_admin(admin_id):
            msg += "━━━━━━━━━━━━━━━━━━━━\n"
            msg += "*💡 Management Commands:*\n"
            msg += "• `/addadmin <user_id> <username>` - Add admin\n"
            msg += "• `/removeadmin <user_id>` - Remove admin\n"
            msg += "• `/setapprovalgroup <admin_id> <group_id>` - Set approval group"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    finally:
        cur.close()
        conn.close()

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = str(update.effective_user.id)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
    row = cur.fetchone()
    if row:
        user = row["data"]
    else:
        user = DEFAULT_USER.copy()
        cur.execute("INSERT INTO users (user_id, data) VALUES (%s, %s);", (uid, Json(user)))
        conn.commit()

    if user.get("banned", False):
        await update.message.reply_text(
            "🚫 *Account Banned*\n\n"
            "Your account has been permanently banned from using this bot.\n\n"
            "If you believe this is a mistake, please contact support.",
            parse_mode="Markdown"
        )
        cur.close()
        conn.close()
        return

    if user.get("setup_in_progress"):
        step = user.get("setup_step", 0)
        if step < len(SETUP_KEYS_ORDER):
            key, desc = SETUP_KEYS_ORDER[step]
            value = text.strip()
            if key == "approval_group_id":
                if update.message.forward_from_chat:
                    value = str(update.message.forward_from_chat.id)
                value = value.strip().replace(" ", "")
                if "t.me" in value.lower():
                    await update.message.reply_text("Send just Group ID like -1001234567890, not link. Or forward a message from group.")
                    cur.close()
                    conn.close()
                    return
                try:
                    group_id = int(value)
                    update_setting(key, str(group_id))
                    set_admin_approval_group(uid, group_id)
                except ValueError:
                    await update.message.reply_text(f"Invalid ID: {value}. Send -100... number or forward message from group.")
                    cur.close()
                    conn.close()
                    return
            elif key in ["task_pay", "withdrawal_limit"]:
                try:
                    val = float(value.replace("$","").strip())
                    if val <= 0:
                        raise ValueError()
                    update_setting(key, str(val))
                except:
                    await update.message.reply_text(f"Invalid amount for {key}. Send number like 0.10")
                    cur.close()
                    conn.close()
                    return
            elif key == "bot_username":
                v = value.replace("@","").strip()
                if len(v) < 3:
                    await update.message.reply_text("Username too short")
                    cur.close()
                    conn.close()
                    return
                update_setting(key, v)
            elif key == "announce_channel":
                v = value.strip().replace("https://t.me/", "").replace("t.me/", "")
                update_setting(key, v)
            elif key == "support_bot":
                v = text.strip()
                if "http" in v.lower():
                    update_setting(key, v)
                else:
                    update_setting(key, v.replace("@",""))
            elif key == "bot_name":
                v = value.strip()
                if len(v) < 2:
                    await update.message.reply_text("Bot name too short")
                    cur.close()
                    conn.close()
                    return
                update_setting(key, v)
            else:
                update_setting(key, value)
            user["setup_step"] = step + 1
            if user["setup_step"] < len(SETUP_KEYS_ORDER):
                next_key, next_desc = SETUP_KEYS_ORDER[user["setup_step"]]
                cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
                conn.commit()
                cur.close()
                conn.close()
                await update.message.reply_text(f"Saved {key} = {value} Step {user['setup_step']+1}/{len(SETUP_KEYS_ORDER)}: Send your {next_desc}")
                return
            else:
                user["setup_in_progress"] = False
                user["setup_step"] = len(SETUP_KEYS_ORDER)
                cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
                conn.commit()
                update_setting("setup_completed", "true")
                cur.close()
                conn.close()
                conn2 = get_conn()
                cur2 = conn2.cursor()
                cur2.execute("SELECT key, value FROM bot_settings ORDER BY key;")
                rows = cur2.fetchall()
                summary = "Setup Complete! Bot ready.\n"
                for k,v in rows:
                    summary += f"{k}: {v}\n"
                summary += "\nCommands: /setapprovalgroup, /settaskpay, /setwithdrawal, /setbotusername, /setannounce, /setsupportbot, /setbotname, /settings, /broadcast"
                cur2.close()
                conn2.close()
                await update.message.reply_text(summary, reply_markup=main_menu())
                return
    if user.get("awaiting_approval_group") and is_admin(uid):
        value = text.strip()
        if update.message.forward_from_chat:
            value = str(update.message.forward_from_chat.id)
        try:
            group_id = int(value.replace(" ",""))
            set_admin_approval_group(uid, group_id)
            update_setting('approval_group_id', str(group_id))
            user["awaiting_approval_group"] = False
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
            await update.message.reply_text(f"Approval Group set to {group_id}")
            cur.close()
            conn.close()
            return
        except:
            await update.message.reply_text("Invalid ID. Send -100... or forward message from group")
            cur.close()
            conn.close()
            return


    if user.get("awaiting_new_task") and is_admin(uid):
        if text.count("\n") < 3:
            await update.message.reply_text("🚨 Wrong task format. Please send:\n\nFirst name: John\nLast name: ✖️\nEmail: example@gmail.com\nPassword: Abc123\n\nOr send /cancel to cancel.")
            cur.close()
            conn.close()
            return
        
        task_id = int(time.time() * 1000) + random.randint(1, 1000)
        admin_id = user.get("task_creator_admin", uid)
        approval_group_id = get_admin_approval_group(admin_id)
        
        cur.execute(
            "INSERT INTO tasks (id, content, status, created_by, approval_group_id) VALUES (%s, %s, %s, %s, %s);",
            (task_id, text, "available", admin_id, approval_group_id)
        )
        user["awaiting_new_task"] = False
        user["task_creator_admin"] = None
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        log_activity(uid, "task_added", f"Task {task_id} added by admin {uid}", {"task_id": task_id})
        
        await update.message.reply_text(
            f"✅ Task added successfully!\n\n"
            f"🆔 Task ID: `{task_id}`\n"
            f"📋 Status: Available\n"
            f"👤 Created by: Admin {admin_id}\n"
            f"📢 Approval Group: {approval_group_id}",
            parse_mode="Markdown"
        )
        cur.close()
        conn.close()
        return

    if text == "🔙 Return" or text == "🔙 Back":
        user["in_withdraw_menu"] = False
        user["in_balance_menu"] = False
        user["in_profile_menu"] = False
        user["in_settings_menu"] = False
        user["in_support_menu"] = False
        user["in_refer_menu"] = False
        user["in_myaccount_menu"] = False
        user["in_activities_menu"] = False
        user["awaiting_withdraw_amount"] = False
        user["awaiting_mpesa"] = False
        user["pending_withdraw_amount"] = 0.0
        user["awaiting_payment_method"] = False
        user["awaiting_payment_address"] = False
        user["selected_payment_method"] = None
        user["choosing_timezone_continent"] = False
        user["choosing_timezone"] = False
        user["selected_continent"] = None
        user["selecting_currency"] = False
        user["currencies_list"] = None
        user["tasks_page"] = 0
        user["history_page"] = 0
        user["activities_page"] = 0
        user["awaiting_withdrawal_selection"] = False
        user["awaiting_new_task"] = False
        user["task_creator_admin"] = None
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        await update.message.reply_text("⬅️ Main Hub", reply_markup=main_menu())
        cur.close()
        conn.close()
        return

    locked_until = user.get("locked_until") or 0
    if locked_until > time.time():
        remaining = int((locked_until - time.time()) / 3600)
        await update.message.reply_text(f"🔒 *Account Temporarily Locked*\n\nYou have reached the maximum number of warnings.\n⏳ Try again in approximately *{remaining} hours*.", parse_mode="Markdown")
        cur.close()
        conn.close()
        return

    # Reset submenu flags when Start Earning is clicked - fixes invalid selection stuck state
    if "start earning" in text.lower() or "get new job" in text.lower() or text == "⚡ Get New Job" or text == "⚡ Start Earning" or text == "🚀 Start Earning":
        user["in_withdraw_menu"] = False
        user["awaiting_withdrawal_selection"] = False
        user["awaiting_payment_address"] = False
        user["awaiting_approval_group"] = False
        user["in_myaccount_menu"] = False
        user["in_balance_menu"] = False
        user["in_refer_menu"] = False
        user["in_support_menu"] = False
        user["in_settings_menu"] = False
        user["in_profile_menu"] = False
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        now = time.time()
        cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
        row = cur.fetchone()
        user = row["data"]
        if now - user.get("last_task_time", 0) < TASK_INTERVAL_SECONDS:
            await update.message.reply_text("⏳⚡️ Slow down champ! Please wait 20 sec before grabbing another job 💼")
            cur.close()
            conn.close()
            return
        synced_count = await sync_user_tasks(uid, user, cur)
        if synced_count > 0:
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
        active_count = await get_active_task_count(uid, user, cur)
        if active_count >= 2:
            cur.execute("SELECT COUNT(*) FROM tasks WHERE assigned_to = %s AND status = 'assigned' AND assigned_at < %s", (uid, now - TASK_TIMEOUT_SECONDS))
            expired_count = cur.fetchone()["count"]
            if expired_count > 0:
                await update.message.reply_text(f"⚠️ *Task Limit Reached*\n\nYou currently have *{active_count} active tasks*.\nHowever, *{expired_count} of your tasks have expired* and are no longer accessible.\n\nPlease wait a moment while we clean up your expired tasks, then try again.", parse_mode="Markdown")
                await admin_fix_stuck(update, context)
            else:
                await update.message.reply_text("⚠️💼 *JOB LIMIT HIT*\n\nYou are holding *2 active jobs*! 😮‍💨\n📤 Deliver or Drop a job before grabbing a new one.\n\nCheck *📦 Active Jobs* 🔍", parse_mode="Markdown")
            cur.close()
            conn.close()
            return
        
        cur.execute("SELECT * FROM tasks WHERE status = 'available' ORDER BY RANDOM() LIMIT 1 FOR UPDATE SKIP LOCKED;")
        task = cur.fetchone()
        
        if not task:
            await update.message.reply_text("😔💤 No jobs available at the moment!\n✨ New premium jobs drop every few minutes - stay tuned! 🚀")
            cur.close()
            conn.close()
            return
        
        cur.execute("UPDATE tasks SET status = 'assigned', assigned_to = %s, assigned_at = %s WHERE id = %s;", (uid, now, task["id"]))
        user["active_task_id"] = task["id"]
        user["last_task_time"] = now
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        log_activity(uid, "task_grab", f"Grabbed task {task['id']} from admin {task.get('created_by', 'Unknown')}", {"task_id": task['id']})
        task_pay = get_task_pay()
        await update.message.reply_text(
            f"💼 NEW JOB AVAILABLE 💼\n\n💰 Reward: $0.08 - $0.25 per completed task (Base ${task_pay:.2f})\n\n📧 Register a Gmail using below data\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{task['content']}\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🔐 Use the exact details provided, otherwise the account will not be paid.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📤 Deliver Job", callback_data="task_submit"), InlineKeyboardButton("🗑️ Drop Job", callback_data="task_cancel")], [InlineKeyboardButton("📖 Task Guide", callback_data="howto")]])
        )
        cur.close()
        conn.close()
        return

    if text == "👑 My Dashboard":
        user["in_myaccount_menu"] = True
        user["in_profile_menu"] = True
        user["in_withdraw_menu"] = False
        user["in_balance_menu"] = False
        user["in_settings_menu"] = False
        user["in_support_menu"] = False
        user["in_refer_menu"] = False
        user["tasks_page"] = 0
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        await update.message.reply_text("👑 *MY DASHBOARD* 👑\n\n✨ Manage your empire below:", parse_mode="Markdown", reply_markup=profile_menu())
        cur.close()
        conn.close()
        return

    if user.get("in_myaccount_menu") and text == "📈 My Performance":
        await show_user_stats(update, context, uid, user)
        cur.close()
        conn.close()
        return

    if user.get("in_myaccount_menu") and text == "📦 Active Jobs":
        user["tasks_page"] = 0
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        await show_tasks_page(update, context, uid, user, 0)
        cur.close()
        conn.close()
        return

    if user.get("in_myaccount_menu") and text == "📊 Activity Log":
        user["in_activities_menu"] = True
        user["activities_page"] = 0
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        await show_activities(update, context, uid, user, 0)
        cur.close()
        conn.close()
        return

    if text == "💎 My Wallet":
        user["in_balance_menu"] = True
        user["in_withdraw_menu"] = False
        user["in_support_menu"] = False
        user["in_refer_menu"] = False
        user["in_myaccount_menu"] = False
        user["awaiting_withdraw_amount"] = False
        user["awaiting_payment_method"] = False
        user["awaiting_payment_address"] = False
        user["pending_withdraw_amount"] = 0.0
        user["history_page"] = 0
        user["awaiting_withdrawal_selection"] = False
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        available_usd = float(user.get("available_balance", 0))
        onhold_usd = float(user.get("onhold_balance", 0))
        affiliate_onhold = user.get("affiliate_onhold_balance", 0.0)
        withdrawal_onhold = user.get("withdrawal_onhold_balance", 0.0)
        user_currency = user.get("currency", "USD")
        cur.execute("SELECT rate_to_usd FROM currencies WHERE code = %s;", (user_currency,))
        result = cur.fetchone()
        rate = float(result["rate_to_usd"]) if result and result.get("rate_to_usd") else 1.0
        available_local = available_usd * rate
        onhold_local = onhold_usd * rate
        affiliate_local = affiliate_onhold * rate
        withdrawal_local = withdrawal_onhold * rate
        withdrawal_limit = get_withdrawal_limit()
        await update.message.reply_text(
            f"💎 *MY WALLET* 💎\n\n"
            f"Available: ${available_usd:.2f} (~ {available_local:.2f} {user_currency})\n"
            f"Task On-hold: ${onhold_usd:.2f} (~ {onhold_local:.2f} {user_currency})\n"
            f"Withdrawal On-Hold: ${withdrawal_onhold:.2f} (~ {withdrawal_local:.2f} {user_currency})\n"
            f"Affiliate On-Hold: ${affiliate_onhold:.2f} (~ {affiliate_local:.2f} {user_currency})\n"
            f"Min Withdrawal: ${withdrawal_limit:.2f}\n\n"
            f"What would you like to do?",
            parse_mode="Markdown",
            reply_markup=earnings_menu()
        )
        cur.close()
        conn.close()
        return

    if text == "💸 Cash Out":
        balance = float(user.get("available_balance", 0))
        withdrawal_limit = get_withdrawal_limit()
        
        if balance < withdrawal_limit:
            await update.message.reply_text(
                f"❌ *Insufficient Balance*\n\n"
                f"💰 Your balance: *${balance:.2f}*\n"
                f"📌 Minimum withdrawal: *${withdrawal_limit:.2f}*\n\n"
                f"Complete more tasks to reach the minimum withdrawal amount.",
                parse_mode="Markdown"
            )
            cur.close()
            conn.close()
            return
        
        user["awaiting_withdrawal_selection"] = True
        user["in_withdraw_menu"] = True
        user["in_balance_menu"] = False
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        
        keyboard = []
        for key in PAYMENT_METHODS_LIST:
            method = PAYMENT_METHODS[key]
            keyboard.append([method["name"]])
        keyboard.append(["🔙 Return"])
        
        await update.message.reply_text(
            f"💳 *Select Withdrawal Method*\n\n"
            f"💰 Available Balance: *${balance:.2f}*\n"
            f"📌 Minimum Withdrawal: *${withdrawal_limit:.2f}*\n\n"
            f"Choose your preferred payment method:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        cur.close()
        conn.close()
        return

    if user.get("awaiting_withdrawal_selection"):
        if text == "🔙 Return" or text == "🔙 Back":
            user["awaiting_withdrawal_selection"] = False
            user["in_withdraw_menu"] = False
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
            await update.message.reply_text("⬅️ Earnings Menu", reply_markup=earnings_menu())
            cur.close()
            conn.close()
            return
        
        selected = None
        for key, method in PAYMENT_METHODS.items():
            if text == method["name"]:
                selected = key
                break
        
        if not selected:
            await update.message.reply_text("❌ Invalid selection. Please choose a payment method from the list.")
            cur.close()
            conn.close()
            return
        
        balance = float(user.get("available_balance", 0))
        withdrawal_limit = get_withdrawal_limit()
        if balance < withdrawal_limit:
            user["awaiting_withdrawal_selection"] = False
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
            await update.message.reply_text(
                f"❌ *Insufficient Balance*\n\n"
                f"💰 Your balance: *${balance:.2f}*\n"
                f"📌 Minimum withdrawal: *${withdrawal_limit:.2f}*\n\n"
                f"Your balance has changed. Please try again.",
                parse_mode="Markdown",
                reply_markup=earnings_menu()
            )
            cur.close()
            conn.close()
            return
        
        user["selected_payment_method"] = selected
        user["awaiting_withdrawal_selection"] = False
        user["awaiting_payment_address"] = True
        method = PAYMENT_METHODS[selected]
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        
        await update.message.reply_text(
            f"💳 *{method['name']} Withdrawal*\n\n"
            f"💰 Amount: *${balance:.2f}*\n"
            f"💸 Fee: *{method['fee_percentage']}%*\n"
            f"📥 You Receive: *${balance - (method['fee_percentage']/100 * balance):.2f}*\n\n"
            f"📝 Enter your {method['name']} address:\n\n"
            f"Format: {method['address_format']}\n"
            f"Example: {method['address_example']}\n\n"
            f"Or click 🔙 Back to cancel.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([["🔙 Return"]], resize_keyboard=True)
        )
        cur.close()
        conn.close()
        return

    if user.get("awaiting_payment_address"):
        if text == "🔙 Return" or text == "🔙 Back":
            user["awaiting_payment_address"] = False
            user["selected_payment_method"] = None
            user["awaiting_withdrawal_selection"] = False
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
            await update.message.reply_text("⬅️ Withdrawal Cancelled", reply_markup=earnings_menu())
            cur.close()
            conn.close()
            return
        
        method_key = user.get("selected_payment_method")
        method = PAYMENT_METHODS.get(method_key)
        if not method:
            await update.message.reply_text("❌ Invalid payment method. Please start over.")
            user["awaiting_payment_address"] = False
            user["selected_payment_method"] = None
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
            cur.close()
            conn.close()
            return
        
        address = text.strip()
        if not re.fullmatch(method["address_regex"], address):
            await update.message.reply_text(
                f"❌ *Invalid Address Format*\n\n"
                f"Format: {method['address_format']}\n"
                f"Example: {method['address_example']}\n\n"
                f"Please enter a valid address or click 🔙 Back to cancel.",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup([["🔙 Return"]], resize_keyboard=True)
            )
            cur.close()
            conn.close()
            return
        
        balance = float(user.get("available_balance", 0))
        withdrawal_limit = get_withdrawal_limit()
        if balance < withdrawal_limit:
            user["awaiting_payment_address"] = False
            user["selected_payment_method"] = None
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
            await update.message.reply_text(
                f"❌ *Insufficient Balance*\n\n"
                f"💰 Your balance: *${balance:.2f}*\n"
                f"📌 Minimum withdrawal: *${withdrawal_limit:.2f}*\n\n"
                f"Your balance has changed. Please try again.",
                parse_mode="Markdown",
                reply_markup=earnings_menu()
            )
            cur.close()
            conn.close()
            return
        
        fee = (method["fee_percentage"] / 100) * balance
        final_amount = balance - fee
        
        if method_key == "mpesa":
            conversation_id = f"B2C_{int(time.time())}_{uid[-4:]}"
            cur.execute("""
                INSERT INTO withdrawals 
                (user_id, amount, final_amount, payment_method, address, status, created_at, mpesa_conversation_id) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) 
                RETURNING id;
            """, (uid, balance, final_amount, method['name'], address, "pending_mpesa", datetime.utcnow(), conversation_id))
            wid = cur.fetchone()["id"]
            
            balance_before = user.get("available_balance", 0.0)
            user["available_balance"] = 0.0
            user["withdrawal_onhold_balance"] = user.get("withdrawal_onhold_balance", 0.0) + balance
            
            usd_to_kes = get_usd_to_kes_rate()
            amount_kes = get_mpesa_amount(final_amount, usd_to_kes)
            
            mpesa_response = send_mpesa_b2c_direct(
                phone_number=address,
                amount_kes=amount_kes,
                conversation_id=conversation_id,
                user_id=uid
            )
            
            if mpesa_response.get("error") or mpesa_response.get("ResponseCode") != "0":
                error_msg = mpesa_response.get("error") or mpesa_response.get("ResponseDescription") or "Unknown error"
                logger.error(f"M-Pesa B2C immediate failure for user {uid}: {error_msg}")
                
                user["available_balance"] = balance_before + balance
                user["withdrawal_onhold_balance"] = max(0, user.get("withdrawal_onhold_balance", 0.0) - balance)
                cur.execute("UPDATE withdrawals SET status = 'failed', mpesa_result = %s WHERE id = %s;", 
                           (error_msg, wid))
                cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
                conn.commit()
                
                await update.message.reply_text(
                    f"❌ M-Pesa Withdrawal Failed\n\n"
                    f"Could not initiate M-Pesa payment.\n"
                    f"Reason: {error_msg}\n\n"
                    f"💰 ${balance:.2f} has been refunded to your balance.\n\n"
                    f"Please try again later or use a different payment method.",
                    reply_markup=main_menu()
                )
                cur.close()
                conn.close()
                return
            
            if mpesa_response.get("ConversationID"):
                cur.execute("UPDATE withdrawals SET mpesa_conversation_id = %s WHERE id = %s;", 
                           (mpesa_response.get("ConversationID"), wid))
            
            if mpesa_response.get("OriginatorConversationID"):
                cur.execute("UPDATE withdrawals SET mpesa_transaction_id = %s WHERE id = %s;", 
                           (mpesa_response.get("OriginatorConversationID"), wid))
            
            b2c_data = {
                'conversation_id': mpesa_response.get('ConversationID'),
                'originator_conversation_id': mpesa_response.get('OriginatorConversationID'),
                'response_code': mpesa_response.get('ResponseCode'),
                'response_description': mpesa_response.get('ResponseDescription'),
                'amount': amount_kes,
                'party_b': address,
                'command_id': MPESA_COMMAND_ID,
                'request_data': {
                    'user_id': uid,
                    'withdrawal_id': wid,
                    'amount_usd': balance,
                    'amount_kes': amount_kes
                }
            }
            store_b2c_request(b2c_data)
            
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
            
            log_transaction(
                uid,
                "withdrawal",
                balance,
                balance_before,
                0.0,
                f"Withdrawal #{wid} requested - M-Pesa (Processing)",
                str(wid)
            )
            
            log_activity(uid, "withdrawal_requested", f"Withdrawal #{wid} requested - M-Pesa", 
                        {"amount": balance, "final": final_amount, "conversation_id": conversation_id})
            
            await update.message.reply_text(
                f"🟡 *M-Pesa Withdrawal Processing*\n\n"
                f"💰 Amount: ${balance:.2f}\n"
                f"💸 Fee: ${fee:.2f}\n"
                f"📥 You Receive: ${final_amount:.2f} (~ {amount_kes:,} KES)\n"
                f"📱 Phone: {address}\n"
                f"🆔 Ref: {conversation_id}\n\n"
                f"⏳ You will receive a confirmation once processed.\n"
                f"📌 Funds are on-hold until payment confirmation.",
                parse_mode="Markdown",
                reply_markup=main_menu()
            )
            
            mpesa_admin_message = (
                f"🏧 *M-Pesa Withdrawal Request*\n"
                f"👤 User: `{uid}`\n"
                f"💰 Amount: *${balance:.2f}* (KSh {amount_kes:,})\n"
                f"📥 Final: *${final_amount:.2f}*\n"
                f"📱 Phone: `{address}`\n"
                f"🆔 Ref: `{conversation_id}`\n\n"
                f"⏳ Processing - Awaiting callback..."
            )
            await notify_all_admins_withdrawal(context.bot, mpesa_admin_message, parse_mode="Markdown")
            
            cur.close()
            conn.close()
            return
        
        else:
            cur.execute("INSERT INTO withdrawals (user_id, amount, final_amount, payment_method, address, status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id;", 
                       (uid, balance, final_amount, method['name'], address, "pending", datetime.utcnow()))
            wid = cur.fetchone()["id"]
            balance_before = user.get("available_balance", 0.0)
            user["available_balance"] = 0.0
            user["awaiting_payment_address"] = False
            user["selected_payment_method"] = None
            user["awaiting_withdrawal_selection"] = False
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
            
            log_transaction(
                uid,
                "withdrawal",
                balance,
                balance_before,
                0.0,
                f"Withdrawal #{wid} requested - {method['name']}",
                str(wid)
            )
            
            log_activity(uid, "withdrawal_requested", f"Withdrawal #{wid} requested", {"amount": balance, "final": final_amount, "method": method['name']})
            await update.message.reply_text(
                f"🟡 *Withdrawal Submitted!*\n\n"
                f"💰 Amount: ${balance:.2f}\n"
                f"💸 Fee: ${fee:.2f}\n"
                f"📥 You Receive: ${final_amount:.2f}\n\n"
                f"⏳ Your withdrawal is pending admin approval.",
                parse_mode="Markdown",
                reply_markup=main_menu()
            )
            manual_admin_message = (
                f"🏧 *Withdrawal Request*\n"
                f"👤 User: `{uid}`\n"
                f"💳 Method: *{method['name']}*\n"
                f"💰 Amount: *${balance:.2f}*\n"
                f"📥 Final: *${final_amount:.2f}*\n"
                f"📍 Address: `{address}`"
            )
            manual_withdrawal_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve_{wid}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"wd_reject_{wid}")
            ]])
            await notify_all_admins_withdrawal(
                context.bot,
                manual_admin_message,
                parse_mode="Markdown",
                reply_markup=manual_withdrawal_markup
            )
            cur.close()
            conn.close()
            return

    if text == "🧾 Payment Log":
        user["history_page"] = 0
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        await show_transaction_history(update, context, uid, user, 0)
        cur.close()
        conn.close()
        return

    if text == "🎁 Bonus Hub":
        user["in_refer_menu"] = True
        user["in_support_menu"] = False
        user["in_myaccount_menu"] = False
        user["in_balance_menu"] = False
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        referral_bonus = get_referral_bonus()
        await update.message.reply_text(
            f"💲 *Rewards Program* 💲\n\n"
            f"🎁 Earn ${referral_bonus:.2f} for each friend who joins!\n"
            f"📌 Bonus goes to Affiliate On-Hold until they complete their first task\n"
            f"👥 Track your referrals and earnings below.\n"
            f"👇 Choose an option:",
            parse_mode="Markdown",
            reply_markup=rewards_menu()
        )
        cur.close()
        conn.close()
        return

    if user.get("in_refer_menu") and text == "🔗 Invite Link":
        bot_username = get_bot_username()
        referral_bonus = get_referral_bonus()
        bot_name = get_bot_name()
        announce_channel = get_announce_channel()
        await update.message.reply_text(
            f"📧 *{bot_name} – Simple Online Tasks*\n\n"
            f"✅ Legit & Instant Payments\n"
            f"💰 Earn ${get_task_pay():.2f} per task\n"
            f"🎁 ${referral_bonus:.2f} per referral (released after first task)\n"
            f"💸 Withdraw at ${get_withdrawal_limit():.2f} minimum\n"
            f"📢 Join our channel: https://t.me/{announce_channel}\n\n"
            f"🔗 Share your referral link:\n\n"
            f"https://t.me/{bot_username}?start={uid}",
            parse_mode="Markdown"
        )
        cur.close()
        conn.close()
        return

    if user.get("in_refer_menu") and text == "👥 My Team":
        await handle_referral_summary(update, context, uid)
        cur.close()
        conn.close()
        return

    if text == "🔧 Control Panel":
        user["in_settings_menu"] = True
        user["in_profile_menu"] = False
        user["in_withdraw_menu"] = False
        user["in_balance_menu"] = False
        user["in_support_menu"] = False
        user["in_refer_menu"] = False
        user["in_myaccount_menu"] = False
        user["choosing_timezone_continent"] = False
        user["choosing_timezone"] = False
        user["selected_continent"] = None
        user["selecting_currency"] = False
        user["currencies_list"] = None
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        current_tz = user.get('timezone', DEFAULT_TIMEZONE)
        tz_display = format_timezone_simple(current_tz)
        await update.message.reply_text(f"⚙️ *Preferences Menu*\n\nCurrent Timezone: *{tz_display}* 🕒\nCurrent Currency: {user.get('currency', 'USD')}\n\nChoose an option below:", parse_mode="Markdown", reply_markup=preferences_menu())
        cur.close()
        conn.close()
        return

    if user.get("in_settings_menu") and text == "💱 Display Currency":
        await show_currency_menu(update, context, user)
        cur.close()
        conn.close()
        return

    if user.get("selecting_currency"):
        currencies = user.get('currencies_list')
        if not currencies:
            cur.execute("SELECT code, name FROM currencies ORDER BY code;")
            currencies = cur.fetchall()
            user['currencies_list'] = currencies
        if text == "🔙 Return" or text == "🔙 Back":
            user["selecting_currency"] = False
            user["currencies_list"] = None
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
            await update.message.reply_text("⚙️ Preferences Menu", reply_markup=preferences_menu())
            cur.close()
            conn.close()
            return
        if " - " in text:
            selected_code = text.split(" - ")[0]
            valid_codes = [c[0] for c in currencies]
            if selected_code in valid_codes:
                user["currency"] = selected_code
                user["selecting_currency"] = False
                user["currencies_list"] = None
                currency_name = ""
                for code, name in currencies:
                    if code == selected_code:
                        currency_name = name
                        break
                cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
                conn.commit()
                log_activity(uid, "settings_change", f"Currency changed to {selected_code}", {"currency": selected_code})
                await update.message.reply_text(f"✅ *Currency Updated Successfully!*\n\nYour preferred currency has been set to:\n`{selected_code} - {currency_name}`\n\nAll balances will now be displayed in {selected_code}.", parse_mode="Markdown", reply_markup=preferences_menu())
                cur.close()
                conn.close()
                return
            else:
                await update.message.reply_text("❌ Invalid currency selection.")
                cur.close()
                conn.close()
                return
        else:
            keyboard = currency_menu(currencies)
            await update.message.reply_text("Please select a currency from the list below or click 🔙 Back to return to preferences.", reply_markup=keyboard)
            cur.close()
            conn.close()
            return

    if user.get("in_settings_menu") and text == "🌐 Timezone":
        user["choosing_timezone_continent"] = True
        user["choosing_timezone"] = False
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        await update.message.reply_text("🌍 *Select Your Continent*\n\nChoose your continent to see all available timezones:", parse_mode="Markdown", reply_markup=timezone_continent_menu())
        cur.close()
        conn.close()
        return

    if user.get("choosing_timezone_continent"):
        if text == "🔙 Return" or text == "🔙 Back":
            user["choosing_timezone_continent"] = False
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
            await update.message.reply_text("⚙️ *Preferences Menu*", parse_mode="Markdown", reply_markup=preferences_menu())
            cur.close()
            conn.close()
            return
        valid_continents = ["🌍 Africa", "🌎 America", "❄️ Antarctica", "🌏 Asia", "🌊 Atlantic", "🦘 Australia", "🇪🇺 Europe", "🌴 Indian", "🌺 Pacific", "🧊 Arctic", "🕒 UTC"]
        if text in valid_continents:
            user["selected_continent"] = text
            user["choosing_timezone_continent"] = False
            user["choosing_timezone"] = True
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
            continent_map = {"🌍 Africa": "Africa", "🌎 America": "America", "❄️ Antarctica": "Antarctica", "🌏 Asia": "Asia", "🌊 Atlantic": "Atlantic", "🦘 Australia": "Australia", "🇪🇺 Europe": "Europe", "🌴 Indian": "Indian", "🌺 Pacific": "Pacific", "🧊 Arctic": "Arctic", "🕒 UTC": "UTC"}
            raw_continent = continent_map.get(text, text)
            timezone_count = len(ALL_TIMEZONES_WITH_OFFSETS.get(raw_continent, []))
            await update.message.reply_text(f"{text} *Timezones*\n\nFound *{timezone_count} timezones* in this region.\nPlease select your timezone from the list below:", parse_mode="Markdown", reply_markup=timezone_list_menu(text))
            cur.close()
            conn.close()
            return
        else:
            await update.message.reply_text("❌ Invalid continent selection.")
            cur.close()
            conn.close()
            return

    if user.get("choosing_timezone"):
        if text == "🔙 Return" or text == "🔙 Back":
            user["choosing_timezone"] = False
            user["choosing_timezone_continent"] = True
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
            await update.message.reply_text("🌍 *Select Your Continent*\n\nChoose your continent to see all available timezones:", parse_mode="Markdown", reply_markup=timezone_continent_menu())
            cur.close()
            conn.close()
            return
        continent = user.get("selected_continent")
        if continent:
            continent_map = {"🌍 Africa": "Africa", "🌎 America": "America", "❄️ Antarctica": "Antarctica", "🌏 Asia": "Asia", "🌊 Atlantic": "Atlantic", "🦘 Australia": "Australia", "🇪🇺 Europe": "Europe", "🌴 Indian": "Indian", "🌺 Pacific": "Pacific", "🧊 Arctic": "Arctic", "🕒 UTC": "UTC"}
            raw_continent = continent_map.get(continent, continent)
            continent_timezones = ALL_TIMEZONES_WITH_OFFSETS.get(raw_continent, [])
            if text in continent_timezones:
                found_tz = None
                for original_tz in ALL_TIMEZONES[raw_continent]:
                    formatted = format_timezone_with_offset(original_tz)
                    if formatted == text:
                        found_tz = original_tz
                        break
                if found_tz:
                    user["timezone"] = found_tz
                    user["choosing_timezone"] = False
                    user["choosing_timezone_continent"] = False
                    user["selected_continent"] = None
                    cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
                    conn.commit()
                    log_activity(uid, "settings_change", f"Timezone changed to {found_tz}", {"timezone": found_tz})
                    await update.message.reply_text(f"✅ *Timezone Updated Successfully!*\n\nYour new timezone is:\n`{text}`\n\nAll times will now be displayed in your local timezone.", parse_mode="Markdown", reply_markup=preferences_menu())
                else:
                    await update.message.reply_text("❌ Error finding timezone. Please try again.", reply_markup=timezone_list_menu(continent))
                cur.close()
                conn.close()
                return
            else:
                await update.message.reply_text("❌ Invalid timezone selection. Please choose from the list.", reply_markup=timezone_list_menu(continent))
                cur.close()
                conn.close()
                return

    if text == "📞 Help Center":
        user["in_support_menu"] = True
        user["in_refer_menu"] = False
        user["in_profile_menu"] = False
        user["in_settings_menu"] = False
        user["in_balance_menu"] = False
        user["in_myaccount_menu"] = False
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        await update.message.reply_text("💬 *Support Desk*\n\nHow can we help you today?\nChoose an option below:", parse_mode="Markdown", reply_markup=support_menu())
        cur.close()
        conn.close()
        return

    if user.get("in_support_menu") and text == "🎧 Live Support":
        support_bot = get_support_bot()
        await update.message.reply_text(
            f"👤 *Customer Care*\n\nNeed personalized assistance? Contact our support bot directly:\n\n📞 **Support Bot:** @{support_bot}\n\n⏰ **Response Time:** Within 24 hours\n\n📋 **Before contacting support:**\n• Read the FAQs first (your question might already be answered)\n• Have your User ID ready\n• Provide screenshots if reporting an issue\n\nClick the button below to chat with our support bot:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👤 Contact Support", url=f"https://t.me/{support_bot}")],
                [InlineKeyboardButton("🔙 Back to Support", callback_data="back_to_support")]
            ])
        )
        cur.close()
        conn.close()
        return

    if user.get("in_support_menu") and text == "❓ How It Works":
        await update.message.reply_text(
            get_faqs_text_part1(), 
            parse_mode="Markdown", 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Next ▶️", callback_data="faq_part2")]
            ])
        )
        cur.close()
        conn.close()
        return

    if user.get("in_support_menu") and text == "📢 Official Channel":
        announce_channel = get_announce_channel()
        await update.message.reply_text(
            f"📢 *Join Our Channel*\n\nStay updated with the latest news, task updates, and payment proofs!\n\n✅ **What you'll get:**\n• 📅 Daily task updates\n• 💰 Payment proofs\n• 🆕 New features announcements\n• 🎁 Special offers\n\nClick the button below to join:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{announce_channel}")],
                [InlineKeyboardButton("🔙 Back to Support", callback_data="back_to_support")]
            ])
        )
        cur.close()
        conn.close()
        return

    cur.close()
    conn.close()
    
    await update.message.reply_text(
        "❌ *Invalid Selection*\n\n"
        "Please choose an action from the main menu below:",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

async def sync_user_tasks(uid, user, cur):
    if not user.get("active_task_id"):
        return 0
    cur.execute("SELECT id, status FROM tasks WHERE id = %s AND assigned_to = %s", (user["active_task_id"], uid))
    task = cur.fetchone()
    if not task or task["status"] != "assigned":
        old_task_id = user["active_task_id"]
        user["active_task_id"] = None
        return 1 if old_task_id else 0
    return 0

async def get_active_task_count(uid, user, cur):
    await sync_user_tasks(uid, user, cur)
    cur.execute("SELECT COUNT(*) FROM tasks WHERE assigned_to = %s AND status = 'assigned';", (uid,))
    return cur.fetchone()["count"]

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except:
        return
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    uid = str(q.from_user.id)
    cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return
    user = row["data"]

    if user.get("banned", False):
        await q.message.edit_text(
            "🚫 *Account Banned*\n\n"
            "Your account has been permanently banned from using this bot.\n\n"
            "If you believe this is a mistake, please contact support.",
            parse_mode="Markdown"
        )
        cur.close()
        conn.close()
        return

    if q.data.startswith("admin_tasks_page:"):
        page = max(0, int(q.data.split(":")[1]))
        admin_id = str(q.from_user.id)
        if not is_admin(admin_id):
            await q.edit_message_text("❌ Unauthorized")
            cur.close()
            conn.close()
            return

        is_super = is_super_admin(admin_id)
        where = "" if is_super else " WHERE created_by = %s"
        params = () if is_super else (admin_id,)

        if where:
            cur.execute("SELECT COUNT(*) AS c FROM tasks WHERE created_by = %s AND status = 'available'", params)
            available_count = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM tasks WHERE created_by = %s AND status = 'assigned'", params)
            assigned_count = cur.fetchone()["c"]
            cur.execute("SELECT id, content, status, assigned_to, created_by, assigned_at, approval_group_id FROM tasks WHERE created_by = %s AND status IN ('available','assigned') ORDER BY id DESC LIMIT %s OFFSET %s", params + (ITEMS_PER_PAGE, page * ITEMS_PER_PAGE))
        else:
            cur.execute("SELECT COUNT(*) AS c FROM tasks WHERE status = 'available'")
            available_count = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM tasks WHERE status = 'assigned'")
            assigned_count = cur.fetchone()["c"]
            cur.execute("SELECT id, content, status, assigned_to, created_by, assigned_at, approval_group_id FROM tasks WHERE status IN ('available','assigned') ORDER BY id DESC LIMIT %s OFFSET %s", (ITEMS_PER_PAGE, page * ITEMS_PER_PAGE))

        tasks = cur.fetchall()
        total_dashboard = available_count + assigned_count
        total_pages = max(1, (total_dashboard + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        page = min(page, total_pages - 1)
        msg = build_admin_task_dashboard(tasks, available_count, assigned_count, page, total_pages)

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin_tasks_page:{page-1}"))
        nav.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin_tasks_page:{page+1}"))
        await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([nav]))
        cur.close()
        conn.close()
        return

    if q.data == "faq_part1":
        await q.message.edit_text(
            get_faqs_text_part1(), 
            parse_mode="Markdown", 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Next ▶️", callback_data="faq_part2")]
            ])
        )
        cur.close()
        conn.close()
        return

    if q.data == "faq_part2":
        await q.message.edit_text(
            get_faqs_text_part2(), 
            parse_mode="Markdown", 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Prev", callback_data="faq_part1"), 
                 InlineKeyboardButton("Next ▶️", callback_data="faq_part3")]
            ])
        )
        cur.close()
        conn.close()
        return

    if q.data == "faq_part3":
        await q.message.edit_text(
            get_faqs_text_part3(), 
            parse_mode="Markdown", 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Prev", callback_data="faq_part2"), 
                 InlineKeyboardButton("Next ▶️", callback_data="faq_part4")]
            ])
        )
        cur.close()
        conn.close()
        return

    if q.data == "faq_part4":
        await q.message.edit_text(
            get_faqs_text_part4(), 
            parse_mode="Markdown", 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Prev", callback_data="faq_part3")]
            ])
        )
        cur.close()
        conn.close()
        return

    if q.data == "back_to_support":
        await q.message.edit_text("💬 *Support Desk*\n\nHow can we help you today?\nChoose an option below:", parse_mode="Markdown", reply_markup=None)
        await q.message.reply_text("💬 Support Desk", reply_markup=support_menu())
        cur.close()
        conn.close()
        return

    if q.data.startswith("tasks_page:"):
        page = int(q.data.split(":")[1])
        user["tasks_page"] = page
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        await show_tasks_page(update, context, uid, user, page)
        cur.close()
        conn.close()
        return

    if q.data.startswith("history_page:"):
        page = int(q.data.split(":")[1])
        user["history_page"] = page
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        await show_transaction_history(update, context, uid, user, page)
        cur.close()
        conn.close()
        return

    if q.data.startswith("activities_page:"):
        page = int(q.data.split(":")[1])
        user["activities_page"] = page
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        await show_activities(update, context, uid, user, page)
        cur.close()
        conn.close()
        return

    if q.data == "back_to_tasks":
        user["in_myaccount_menu"] = True
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        await q.message.edit_text("👑 *MY DASHBOARD* 👑\n\n✨ Manage your empire below:", parse_mode="Markdown", reply_markup=None)
        await q.message.reply_text("👑 My Dashboard", reply_markup=profile_menu())
        cur.close()
        conn.close()
        return

    if q.data == "back_to_history":
        user["in_balance_menu"] = True
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        await q.message.edit_text("💎 *MY WALLET* 💎\n\nWhat would you like to do?", parse_mode="Markdown", reply_markup=None)
        await q.message.reply_text("💰 Your Earnings", reply_markup=earnings_menu())
        cur.close()
        conn.close()
        return

    if q.data == "back_to_activities":
        user["in_myaccount_menu"] = True
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        await q.message.edit_text("👑 *MY DASHBOARD* 👑\n\n✨ Manage your empire below:", parse_mode="Markdown", reply_markup=None)
        await q.message.reply_text("👑 My Dashboard", reply_markup=profile_menu())
        cur.close()
        conn.close()
        return

    admin_id = get_admin_id_int()
    user_admin_id = str(q.from_user.id)
    
    if q.data.startswith("approve_") or q.data.startswith("reject_") or q.data.startswith("wrong_"):
        if not is_admin(user_admin_id):
            await q.answer("❌ You are not an admin!", show_alert=True)
            cur.close()
            conn.close()
            return
        
        tid = q.data.split("_")[1]
        cur.execute("SELECT * FROM tasks WHERE id = %s;", (tid,))
        t = cur.fetchone()
        if not t:
            await q.answer("❌ Task not found", show_alert=True)
            cur.close()
            conn.close()
            return
        
        is_super = is_super_admin(user_admin_id)
        if not is_super and t.get("created_by") != user_admin_id:
            await q.answer("❌ You don't have permission to approve/reject this task!", show_alert=True)
            cur.close()
            conn.close()
            return
        
        task_uid = t["assigned_to"]
        cur.execute("SELECT data FROM users WHERE user_id = %s;", (task_uid,))
        u_row = cur.fetchone()
        if not u_row:
            await q.answer("❌ User not found", show_alert=True)
            cur.close()
            conn.close()
            return
        
        u = u_row["data"]
        
        if q.data.startswith("approve_"):
            task_pay = get_task_pay()
            daily_bonus = get_daily_bonus()
            today = datetime.now().strftime("%Y-%m-%d")
            
            tasks_completed_before = u.get("tasks_completed", 0)
            
            balance_before = u.get("available_balance", 0.0)
            u["available_balance"] += task_pay
            u["onhold_balance"] -= task_pay
            u["tasks_completed"] = tasks_completed_before + 1
            
            last_task_date = u.get("last_task_date")
            daily_bonus_awarded = False
            daily_bonus_amount = 0.0
            
            if last_task_date != today:
                daily_bonus_amount = daily_bonus
                balance_before_bonus = u["available_balance"]
                u["available_balance"] += daily_bonus_amount
                daily_bonus_awarded = True
                u["last_task_date"] = today
                u["daily_bonus_claimed"] = True
                
                log_transaction(
                    task_uid,
                    "daily_bonus",
                    daily_bonus_amount,
                    balance_before_bonus,
                    u["available_balance"],
                    f"Daily first task bonus for {today}",
                    str(tid)
                )
                
                log_activity(
                    task_uid,
                    "daily_bonus",
                    f"Daily bonus ${daily_bonus_amount:.2f} awarded for first task of the day",
                    {"amount": daily_bonus_amount, "task_id": tid}
                )
            
            log_transaction(
                task_uid,
                "task_approved",
                task_pay,
                balance_before,
                u["available_balance"],
                f"Task {tid} approved and paid",
                str(tid)
            )
            
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(u), task_uid))
            cur.execute("UPDATE tasks SET status = 'approved' WHERE id = %s;", (tid,))
            conn.commit()
            
            log_activity(task_uid, "task_approved", f"Task {tid} approved", {"task_id": tid, "amount": task_pay})
            
            approval_msg = f"💸 *Accrual of funds to the balance*\n\n"
            approval_msg += f"💵 One of your tasks was approved and paid successfully!\n"
            approval_msg += f"💰 *+${task_pay:.2f}* added to your main balance.\n"
            
            if daily_bonus_awarded:
                approval_msg += f"🌟 *Daily Bonus: +${daily_bonus_amount:.2f}* for your first task today! 🎉\n"
            
            approval_msg += f"\n📌 Keep up the great work! Complete more tasks to increase your earnings."
            
            await context.bot.send_message(task_uid, approval_msg, parse_mode="Markdown")
            
            if tasks_completed_before == 0:
                referred_by = u.get("referred_by")
                if referred_by:
                    await release_affiliate_bonus(referred_by, task_uid, context)
            
            task_email = extract_email(t["content"])
            await q.edit_message_text(f"👤 User ID: {task_uid}\n📧 Email: {task_email}\n📝 Status: ✅ Approved\n{'🌟 Daily Bonus: +$' + str(daily_bonus_amount) if daily_bonus_awarded else ''}")
            
            if t.get("created_by") and t.get("created_by") != user_admin_id:
                try:
                    await context.bot.send_message(
                        chat_id=int(t.get("created_by")),
                        text=f"✅ Task {tid} was approved by admin {user_admin_id}\n👤 User: {task_uid}\n💰 Amount: ${task_pay:.2f}"
                    )
                except Exception as e:
                    print(f"Could not notify task creator: {e}")
            
        elif q.data.startswith("reject_"):
            task_pay = get_task_pay()
            
            balance_before = u.get("onhold_balance", 0.0)
            u["onhold_balance"] -= task_pay
            u["warnings"] = u.get("warnings", 0) + 1
            u["total_warnings"] = u.get("total_warnings", 0) + 1
            
            log_transaction(
                task_uid,
                "task_rejected",
                -task_pay,
                balance_before,
                u["onhold_balance"],
                f"Task {tid} rejected - on-hold balance reduced",
                str(tid)
            )
            
            total_warnings = u.get("total_warnings", 0)
            
            if total_warnings >= MAX_TOTAL_WARNINGS:
                u["banned"] = True
                u["banned_at"] = time.time()
                u["banned_by"] = "AUTO_BAN_SYSTEM"
                log_activity(task_uid, "auto_ban", "Auto-banned for reaching 15 total warnings", {"total_warnings": total_warnings})
                ban_message = "🔒 *PERMANENT BAN*\n\nYou have been permanently banned for reaching 15 total warnings.\n\nThis action cannot be undone."
                await context.bot.send_message(task_uid, ban_message, parse_mode="Markdown")
                await q.edit_message_text(f"👤 User ID: {task_uid}\n📧 Email: {extract_email(t['content'])}\n📝 Status: ❌ Rejected\n⚠️ User has been AUTO-BANNED (15 total warnings)")
                cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(u), task_uid))
                conn.commit()
                cur.close()
                conn.close()
                return
            elif u["warnings"] >= MAX_REJECT_WARNINGS:
                u["locked_until"] = time.time() + (24 * 60 * 60)
                user_message = f"🔒 *Account Locked for 24 Hours*\n\nReason: Too many rejected tasks ({u['warnings']}/{MAX_REJECT_WARNINGS}).\nTotal Warnings: {total_warnings}/{MAX_TOTAL_WARNINGS}\n⏱ Try again after 24 hours."
            else:
                user_message = f"🚨 Your task was rejected.\nReason: You did not follow account creation instructions.\n\n⚠️ Rejection Warning {u['warnings']}/{MAX_REJECT_WARNINGS}\n📊 Total Warnings: {total_warnings}/{MAX_TOTAL_WARNINGS}"
            log_activity(task_uid, "task_rejected", f"Task {tid} rejected", {"task_id": tid, "warnings": u['warnings'], "total_warnings": total_warnings})
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(u), task_uid))
            cur.execute("UPDATE tasks SET status = 'available', assigned_to = NULL, assigned_at = NULL WHERE id = %s;", (tid,))
            conn.commit()
            await context.bot.send_message(task_uid, user_message, parse_mode="Markdown")
            task_email = extract_email(t["content"])
            await q.edit_message_text(f"👤 User ID: {task_uid}\n📧 Email: {task_email}\n📝 Status: ❌ Rejected")
            
        elif q.data.startswith("wrong_"):
            task_pay = get_task_pay()
            
            balance_before = u.get("onhold_balance", 0.0)
            u["onhold_balance"] -= task_pay
            u["wrong_warnings"] = u.get("wrong_warnings", 0) + 1
            u["total_warnings"] = u.get("total_warnings", 0) + 1
            
            log_transaction(
                task_uid,
                "task_rejected",
                -task_pay,
                balance_before,
                u["onhold_balance"],
                f"Task {tid} rejected - wrong password",
                str(tid)
            )
            
            total_warnings = u.get("total_warnings", 0)
            
            if total_warnings >= MAX_TOTAL_WARNINGS:
                u["banned"] = True
                u["banned_at"] = time.time()
                u["banned_by"] = "AUTO_BAN_SYSTEM"
                log_activity(task_uid, "auto_ban", "Auto-banned for reaching 15 total warnings", {"total_warnings": total_warnings})
                ban_message = "🔒 *PERMANENT BAN*\n\nYou have been permanently banned for reaching 15 total warnings.\n\nThis action cannot be undone."
                await context.bot.send_message(task_uid, ban_message, parse_mode="Markdown")
                await q.edit_message_text(f"👤 User ID: {task_uid}\n📧 Email: {extract_email(t['content'])}\n🗑 Status: Task Deleted (wrong password)\n⚠️ User has been AUTO-BANNED (15 total warnings)")
                cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(u), task_uid))
                cur.execute("DELETE FROM tasks WHERE id = %s;", (tid,))
                conn.commit()
                cur.close()
                conn.close()
                return
            elif u["wrong_warnings"] >= MAX_WRONG_WARNINGS:
                u["locked_until"] = time.time() + LOCK_DURATION_SECONDS
                lock_message = f"🔒 *Account Locked for 24 Hours*\n\nReason: Too many wrong password attempts.\nTotal Warnings: {total_warnings}/{MAX_TOTAL_WARNINGS}"
                await context.bot.send_message(task_uid, lock_message, parse_mode="Markdown")
            else:
                await context.bot.send_message(task_uid, f"🚫 Task Rejected – Wrong Password used in account creation\n\n⚠️ Password Warning {u['wrong_warnings']}/{MAX_WRONG_WARNINGS}\n📊 Total Warnings: {total_warnings}/{MAX_TOTAL_WARNINGS}", parse_mode="Markdown")
            log_activity(task_uid, "task_rejected", f"Task {tid} rejected (wrong password)", {"task_id": tid, "wrong_warnings": u['wrong_warnings']})
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(u), task_uid))
            cur.execute("DELETE FROM tasks WHERE id = %s;", (tid,))
            conn.commit()
            task_email = extract_email(t["content"])
            await q.edit_message_text(f"👤 User ID: {task_uid}\n📧 Email: {task_email}\n🗑 Status: Task Deleted (wrong password)")
        
        cur.close()
        conn.close()
        return

    if q.data.startswith("wd_approve_"):
        if not is_admin(str(q.from_user.id)):
            await q.answer("❌ You are not an admin!", show_alert=True)
            cur.close()
            conn.close()
            return
        
        wid = q.data.split("_")[2]
        cur.execute("UPDATE withdrawals SET status = 'processed' WHERE id = %s RETURNING user_id, final_amount, amount;", (wid,))
        row = cur.fetchone()
        conn.commit()
        
        log_transaction(
            row["user_id"],
            "withdrawal_approved",
            -float(row["amount"]),
            0.0,
            0.0,
            f"Withdrawal #{wid} approved and processed",
            str(wid)
        )
        
        log_activity(row["user_id"], "withdrawal_approved", f"Withdrawal #{wid} approved", {"amount": row['final_amount']})
        await context.bot.send_message(row["user_id"], f"💰 Withdrawal successful!\n💸 ${row['final_amount']:.2f} has been sent to your account.")
        await q.edit_message_text(f"✅ Approved - ${row['final_amount']:.2f} paid to user")
        cur.close()
        conn.close()
        return

    if q.data.startswith("wd_reject_"):
        if not is_admin(str(q.from_user.id)):
            await q.answer("❌ You are not an admin!", show_alert=True)
            cur.close()
            conn.close()
            return
        
        wid = q.data.split("_")[2]
        cur.execute("SELECT * FROM withdrawals WHERE id = %s;", (wid,))
        w = cur.fetchone()
        cur.execute("UPDATE withdrawals SET status = 'cancelled' WHERE id = %s;", (wid,))
        cur.execute("SELECT data FROM users WHERE user_id = %s;", (w["user_id"],))
        u = cur.fetchone()["data"]
        
        balance_before = u.get("available_balance", 0.0)
        u["available_balance"] = float(u.get("available_balance", 0)) + float(w["amount"])
        
        log_transaction(
            w["user_id"],
            "withdrawal_rejected",
            float(w["amount"]),
            balance_before,
            u["available_balance"],
            f"Withdrawal #{wid} rejected - funds refunded",
            str(wid)
        )
        
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(u), w["user_id"]))
        conn.commit()
        log_activity(w["user_id"], "withdrawal_rejected", f"Withdrawal #{wid} rejected", {"amount": float(w['amount'])})
        await context.bot.send_message(w["user_id"], f"❌ Withdrawal rejected\n💸 ${w['amount']:.2f} refunded to your balance.")
        await q.edit_message_text(f"❌ Rejected - ${w['amount']:.2f}")
        cur.close()
        conn.close()
        return

    if q.data == "admin_settings":
        if not is_admin(str(q.from_user.id)):
            await q.answer("❌ Unauthorized", show_alert=True)
            cur.close()
            conn.close()
            return
        
        await q.edit_message_text(
            "⚙️ *Settings Management*\n━━━━━━━━━━━━━━━━━━━━\n\n"
            "Use these commands to update bot settings:\n\n"
            "📌 `/setbotusername <username>`\n"
            "   Update bot's @username\n\n"
            "📌 `/setsupportbot <username>`\n"
            "   Update support bot @username\n\n"
            "📌 `/setadminid <user_id>`\n"
            "   Update admin user ID\n\n"
            "📌 `/setannounce <channel>`\n"
            "   Update announce channel\n\n"
            "📌 `/setwithdrawal <amount>`\n"
            "   Update minimum withdrawal\n\n"
            "📌 `/settaskpay <amount>`\n"
            "   Update task payment\n\n"
            "📌 `/setrefbonus <amount>`\n"
            "   Update referral bonus\n\n"
            "📌 `/setbotname <name>`\n"
            "   Update bot name\n\n"
            "📌 `/setdailybonus <amount>`\n"
            "   Update daily first task bonus\n\n"
            "📌 `/setusdkesrate <rate>`\n"
            "   Update USD to KES conversion rate\n\n"
            "📌 `/botsettings`\n"
            "   View all current settings",
            parse_mode="Markdown"
        )
        cur.close()
        conn.close()
        return

    if q.data == "admin_userinfo_prompt":
        if not is_admin(str(q.from_user.id)):
            await q.answer("❌ Unauthorized", show_alert=True)
            cur.close()
            conn.close()
            return
        
        await q.edit_message_text("👤 *User Information*\n\nPlease enter the user ID you want to check:\n\nFormat: `/userinfo <user_id>`\nExample: `/userinfo 123456789`\n\nOr click below to return to dashboard:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_to_dashboard")]]))
        cur.close()
        conn.close()
        return

    if q.data == "back_to_dashboard":
        if not is_admin(str(q.from_user.id)):
            await q.answer("❌ Unauthorized", show_alert=True)
            cur.close()
            conn.close()
            return
        
        keyboard = [
            [InlineKeyboardButton("📂 Tasks Overview", callback_data="admin_tasks"), 
             InlineKeyboardButton("💰 User Balances", callback_data="admin_balances")],
            [InlineKeyboardButton("📥 Withdrawals", callback_data="admin_withdrawals"), 
             InlineKeyboardButton("👥 Users", callback_data="admin_users")],
            [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"), 
             InlineKeyboardButton("👤 User Info", callback_data="admin_userinfo_prompt")],
            [InlineKeyboardButton("🚫 Banned Users", callback_data="admin_banned"), 
             InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")]
        ]
        if is_super_admin(str(q.from_user.id)):
            keyboard.append([InlineKeyboardButton("👥 Manage Admins", callback_data="admin_admins")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        admin_username = get_admin_username(str(q.from_user.id)) or str(q.from_user.id)
        is_super = is_super_admin(str(q.from_user.id))
        
        await q.edit_message_text(
            f"🛠 *ADMIN DASHBOARD*\n\n"
            f"👤 Admin: `{admin_username}`\n"
            f"🔰 Status: {'⭐ Super Admin' if is_super else '👤 Admin'}\n\n"
            f"Manage users, tasks & payments from here:\n"
            f"Select an option below:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        cur.close()
        conn.close()
        return

    if q.data == "howto":
        try:
            await q.answer()
            await q.message.reply_text(HOW_TO_REGISTER_TEXT)
        except Exception as e:
            print(f"howto callback error: {e}")
        return


    task = None
    if user.get("active_task_id"):
        cur.execute("SELECT * FROM tasks WHERE id = %s;", (user["active_task_id"],))
        task = cur.fetchone()

    if q.data == "task_cancel" and task:
        cur.execute("UPDATE tasks SET status = 'available', assigned_to = NULL, assigned_at = NULL WHERE id = %s;", (task["id"],))
        user["active_task_id"] = None
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        log_activity(uid, "task_cancel", f"Cancelled task {task['id']}", {"task_id": task['id']})
        await q.edit_message_text("❌ Task cancelled")
        cur.close()
        conn.close()
        return

    if q.data == "task_submit" and task:
        cur.execute("UPDATE tasks SET status = 'submitted' WHERE id = %s;", (task["id"],))
        user["active_task_id"] = None
        task_pay = get_task_pay()
        
        balance_before = user.get("onhold_balance", 0.0)
        user["onhold_balance"] += task_pay
        
        log_transaction(
            uid,
            "task_hold",
            task_pay,
            balance_before,
            user["onhold_balance"],
            f"Task {task['id']} submitted - on-hold balance",
            str(task['id'])
        )
        
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        log_activity(uid, "task_submitted", f"Submitted task {task['id']}", {"task_id": task['id']})
        
        await q.edit_message_text(
            f"📤 *Task Submitted Successfully!*\n\n"
            f"💸 *${task_pay:.2f}* has been credited to your on-hold balance.\n\n"
            f"⏳ Funds will be transferred to the main balance after admin approval.\n"
            f"📛 *Be sure to LOG OUT of the account on your device.*\n\n"
            f"✅ You can track the status of this task in your *📂 My Tasks* section.",
            parse_mode="Markdown"
        )
        
        approval_group_id = task.get("approval_group_id") or get_admin_approval_group(task.get("created_by") or str(get_admin_id_int()))
        
        await context.bot.send_message(
            approval_group_id,
            f"📥 Task Submitted\nUser: {uid}\n\n{task['content']}\n\n👤 Task from Admin: {task.get('created_by', 'Unknown')}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{task['id']}"), 
                 InlineKeyboardButton("❌ Reject", callback_data=f"reject_{task['id']}")], 
                [InlineKeyboardButton("🚫 Wrong Password", callback_data=f"wrong_{task['id']}")]
            ])
        )
        cur.close()
        conn.close()
        return

    cur.close()
    conn.close()

def get_faqs_text_part1() -> str:
    task_pay = get_task_pay()
    daily_bonus = get_daily_bonus()
    withdrawal_limit = get_withdrawal_limit()
    bot_name = get_bot_name()
    return (
        "*❓ FAQ (1/4)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "*📌 GENERAL QUESTIONS*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 *What is {bot_name}?*\n"
        f"{bot_name} is a legitimate online earning platform where you earn real money by completing simple Gmail registration tasks.\n\n"
        "🔹 *Is it really free to join?*\n"
        f"Yes! Joining {bot_name} is completely free.\n\n"
        "🔹 *How do I start earning?*\n"
        "Click *🚀 Start Earning*, copy the details, create the Gmail account, and submit.\n\n"
        "🔹 *How many tasks can I do per day?*\n"
        "As many as you want! But only *2 tasks at a time*.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "*💰 PAYMENTS*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 *Earnings per task?*\n"
        f"*${task_pay:.2f}* per approved account.\n\n"
        f"🔹 *Daily first task bonus?*\n"
        f"Extra *${daily_bonus:.2f}* for your first approved task each day!\n\n"
        f"🔹 *Minimum withdrawal?*\n"
        f"*${withdrawal_limit:.2f}*\n\n"
    )

def get_faqs_text_part2() -> str:
    return (
        "*❓ FAQ (2/4)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "*💰 PAYMENTS (continued)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔹 *Payment methods?*\n"
        "• 📱 MPESA (10% fee)\n"
        "• 💳 PayPal (23% fee)\n"
        "• ⚡ Litecoin - LTC (23% fee)\n"
        "• 💎 USD PEP20 (23% fee)\n\n"
        "🔹 *Withdrawal time?*\n"
        "*24-48 hours* after approval.\n\n"
        "🔹 *Why withdrawal fees?*\n"
        "Covers transaction costs. MPESA has the lowest fee at 10%.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "*📋 TASKS & SUBMISSIONS*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔹 *Tasks at once?*\n"
        "Up to *2 tasks simultaneously*.\n\n"
        "🔹 *Task time limit?*\n"
        "*2 hours* to complete.\n\n"
        "🔹 *Task rejected?*\n"
        "You get a warning. *5 warnings* = 24h lock.\n\n"
    )

def get_faqs_text_part3() -> str:
    referral_bonus = get_referral_bonus()
    return (
        "*❓ FAQ (3/4)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "*📋 TASKS (continued)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔹 *Wrong password warning?*\n"
        "*3 warnings* = 24h lock.\n\n"
        "🔹 *How to submit correctly?*\n"
        "1. Use EXACT details from task\n"
        "2. Create Gmail properly\n"
        "3. NO phone number or recovery email\n"
        "4. Submit immediately\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "*👥 REFERRAL PROGRAM*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 *Referral bonus?*\n"
        f"*${referral_bonus:.2f}* per referral!\n\n"
        "🔹 *When do I get it?*\n"
        "Placed in *Affiliate On-Hold* when they join. Released when they complete their *first task*.\n\n"
        "🔹 *How many referrals?*\n"
        "Unlimited!\n\n"
    )

def get_faqs_text_part4() -> str:
    bot_name = get_bot_name()
    support_bot = get_support_bot()
    return (
        "*❓ FAQ (4/4)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "*⚙️ ACCOUNT SETTINGS*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔹 *Change timezone?*\n"
        "*⚙️ Preferences → 🌍 Time Zone*\n\n"
        "🔹 *Change currency?*\n"
        "*⚙️ Preferences → 💰 Currency*\n\n"
        "🔹 *Currencies supported?*\n"
        "Over 100 currencies including USD, EUR, GBP, KES.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "*🛡️ ACCOUNT SECURITY*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔹 *Is my account secure?*\n"
        "Yes! Secure authentication, no shared info.\n\n"
        "🔹 *Locked out?*\n"
        "Wait 24 hours or contact support.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "*🆘 SUPPORT*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 *Contact support?*\n"
        f"*💬 Support → 👤 Customer Care* or @{support_bot}\n\n"
        "🔹 *What to provide?*\n"
        "Your User ID and clear description. Screenshots help.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *{bot_name} - Earn Money Online!*"
    )

async def show_activities(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str, user: dict, page: int = 0):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute("SELECT COUNT(*) as total FROM user_activities WHERE user_id = %s;", (uid,))
        total_count = cur.fetchone()["total"]
        
        if total_count == 0:
            await update.message.reply_text(
                "📋 *No Activities Yet*\n\n"
                "Your activity history will appear here as you use the bot.\n"
                "Activities tracked include:\n"
                "• Task completions\n"
                "• Withdrawals\n"
                "• Referrals\n"
                "• Account changes\n"
                "• And more!",
                parse_mode="Markdown"
            )
            cur.close()
            conn.close()
            return
        
        total_pages = (total_count + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        offset = page * ITEMS_PER_PAGE
        
        cur.execute(
            "SELECT id, activity_type, description, details, created_at FROM user_activities "
            "WHERE user_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s;",
            (uid, ITEMS_PER_PAGE, offset)
        )
        activities = cur.fetchall()
        
        msg = f"📋 *Your Activities* (Page {page+1}/{total_pages})\n━━━━━━━━━━━━━━━━━━━━\n\n"
        
        activity_icons = {
            "task_completed": "✅",
            "task_submitted": "📤",
            "task_approved": "💰",
            "task_rejected": "❌",
            "withdrawal_requested": "🏧",
            "withdrawal_approved": "✅",
            "withdrawal_rejected": "❌",
            "referral": "🎁",
            "referral_pending": "⏳",
            "affiliate_bonus_released": "🎉",
            "settings_change": "⚙️",
            "registration": "👤",
            "task_grab": "💼",
            "task_cancel": "🚫",
            "daily_bonus": "🌟"
        }
        
        for activity in activities:
            icon = activity_icons.get(activity['activity_type'], "📌")
            activity_time = format_timestamp_with_timezone(activity['created_at'], user)
            desc = activity['description'] or activity['activity_type'].replace('_', ' ').title()
            
            details_text = ""
            if activity['details']:
                details = activity['details']
                if 'amount' in details:
                    details_text = f"\n┃    💰 Amount: ${details['amount']:.2f}"
                if 'task_id' in details:
                    details_text += f"\n┃    🆔 Task: {details['task_id']}"
                if 'referral_id' in details:
                    details_text += f"\n┃    👤 Referral: {details['referral_id']}"
            
            msg += f"{icon} *{desc}*\n"
            msg += f"┃ 🕐 {activity_time}"
            if details_text:
                msg += details_text
            msg += "\n━━━━━━━━━━━━━━━━━━━━\n\n"
            
            if len(msg) > 3500:
                if update.message:
                    await update.message.reply_text(msg, parse_mode="Markdown")
                else:
                    await update.callback_query.message.edit_text(msg, parse_mode="Markdown")
                msg = ""
        
        if msg:
            keyboard = build_tasks_pagination_keyboard(page, total_pages, "activities")
            if update.message:
                await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=keyboard)
            else:
                await update.callback_query.message.edit_text(msg, parse_mode="Markdown", reply_markup=keyboard)
    
    except Exception as e:
        print(f"Error showing activities: {e}")
        await update.message.reply_text("❌ Error loading activities.")
    finally:
        cur.close()
        conn.close()

async def show_user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str, user: dict):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT COUNT(*) FROM users WHERE data->>'referred_by' = %s;", (uid,))
        referrals = cur.fetchone()["count"]
        completed = user.get("tasks_completed", 0)
        warnings = user.get("warnings", 0)
        wrong_warnings = user.get("wrong_warnings", 0)
        total_warnings = user.get("total_warnings", 0)
        affiliate_onhold = user.get("affiliate_onhold_balance", 0.0)
        withdrawal_onhold = user.get("withdrawal_onhold_balance", 0.0)
        daily_bonus = get_daily_bonus()
        
        last_task_date = user.get("last_task_date")
        today = datetime.now().strftime("%Y-%m-%d")
        daily_bonus_available = last_task_date != today
        
        status = "🔓 Active"
        if (user.get("locked_until") or 0) > time.time():
            status = "🔒 Temporarily Locked"
        if user.get("banned", False):
            status = "🚫 Permanently Banned"
        
        await update.message.reply_text(
            f"👤 *Your Profile Stats*\n━━━━━━━━━━━━━━\n\n"
            f"💰 Balance: *${user.get('available_balance', 0):.2f}*\n"
            f"⏳ Task On-hold: *${user.get('onhold_balance', 0):.2f}*\n"
            f"🏧 Withdrawal On-Hold: *${withdrawal_onhold:.2f}*\n"
            f"🎁 Affiliate On-Hold: *${affiliate_onhold:.2f}*\n"
            f"👥 Referrals: *{referrals}*\n"
            f"📊 Tasks Completed: *{completed}*\n"
            f"🌟 Daily Bonus: ${daily_bonus:.2f} {'✅ Available' if daily_bonus_available else '❌ Claimed Today'}\n"
            f"⚠️ Rejection Warnings: {warnings}/{MAX_REJECT_WARNINGS}\n"
            f"🚫 Password Warnings: {wrong_warnings}/{MAX_WRONG_WARNINGS}\n"
            f"📊 Total Warnings: {total_warnings}/{MAX_TOTAL_WARNINGS}\n"
            f"🔐 Status: *{status}*\n\n"
            f"📌 *Withdrawal On-Hold:*\n"
            f"Funds placed on-hold during M-Pesa processing. Refunded automatically if transaction fails.",
            parse_mode="Markdown"
        )
    finally:
        cur.close()
        conn.close()

async def handle_referral_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT COUNT(*) FROM users WHERE data->>'referred_by' = %s;", (uid,))
        total_refs = cur.fetchone()["count"]
        
        cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
        user_row = cur.fetchone()
        user_data = user_row["data"] if user_row else {}
        
        referral_bonus = get_referral_bonus()
        affiliate_onhold = user_data.get("affiliate_onhold_balance", 0.0)
        total_earnings = total_refs * referral_bonus
        
        referred_users_completed = user_data.get("referred_users_completed", [])
        completed_count = len(referred_users_completed)
        pending_count = total_refs - completed_count
        
        if total_refs == 0:
            await update.message.reply_text(
                "📭 *No Referrals Yet*\n\n"
                "🔗 Share your referral link to start earning!\n\n"
                f"🎁 You earn ${referral_bonus:.2f} per referral after they complete their first task.",
                parse_mode="Markdown"
            )
            return
        
        await update.message.reply_text(
            f"👥 *Referral Summary*\n━━━━━━━━━━━━━━\n\n"
            f"👤 Total Referrals: *{total_refs}*\n"
            f"✅ Completed First Task: *{completed_count}*\n"
            f"⏳ Pending Completion: *{pending_count}*\n"
            f"💰 Affiliate On-Hold: *${affiliate_onhold:.2f}*\n"
            f"🎁 Bonus per Referral: *${referral_bonus:.2f}*\n\n"
            f"📌 *How it works:*\n"
            f"• Bonus goes to *Affiliate On-Hold* when they join\n"
            f"• Released to *Main Balance* when they complete their first task\n"
            f"• You earn ${referral_bonus:.2f} per successful referral!",
            parse_mode="Markdown"
        )
    finally:
        cur.close()
        conn.close()

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ *Unknown Command*\n\n"
        "Please use the buttons below to interact with the bot:",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

async def show_transaction_history(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str, user: dict, page: int = 0):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute("SELECT COUNT(*) as total FROM transactions WHERE user_id = %s;", (uid,))
        total_count = cur.fetchone()["total"]
        
        if total_count == 0:
            await update.message.reply_text(
                "📭 *No Transaction History*\n\n"
                "Your transaction history will appear here once you start earning.\n\n"
                "📌 *Transaction types tracked:*\n"
                "• 💰 Task payments (On-hold added)\n"
                "• ✅ Task approvals (Transferred to main balance)\n"
                "• 🏧 Withdrawals\n"
                "• 🎁 Referral bonuses\n"
                "• 📊 Affiliate bonus releases\n"
                "• 🌟 Daily bonus rewards\n\n"
                "Start earning by clicking *🚀 Start Earning*!",
                parse_mode="Markdown"
            )
            cur.close()
            conn.close()
            return
        
        total_pages = (total_count + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        offset = page * ITEMS_PER_PAGE
        
        cur.execute(
            "SELECT id, transaction_type, amount, balance_before, balance_after, description, reference_id, created_at "
            "FROM transactions WHERE user_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s;",
            (uid, ITEMS_PER_PAGE, offset)
        )
        transactions = cur.fetchall()
        
        type_icons = {
            "task_hold": "⏳",
            "task_approved": "✅",
            "task_rejected": "❌",
            "withdrawal": "🏧",
            "withdrawal_approved": "✅",
            "withdrawal_rejected": "❌",
            "affiliate_bonus": "🎁",
            "affiliate_bonus_release": "🎉",
            "admin_add": "➕",
            "admin_reduce": "➖",
            "referral_bonus": "👥",
            "daily_bonus": "🌟"
        }
        
        type_names = {
            "task_hold": "Task On-Hold",
            "task_approved": "Task Approved",
            "task_rejected": "Task Rejected",
            "withdrawal": "Withdrawal",
            "withdrawal_approved": "Withdrawal Approved",
            "withdrawal_rejected": "Withdrawal Rejected",
            "affiliate_bonus": "Affiliate Bonus",
            "affiliate_bonus_release": "Affiliate Released",
            "admin_add": "Admin Added",
            "admin_reduce": "Admin Reduced",
            "referral_bonus": "Referral Bonus",
            "daily_bonus": "🌟 Daily Bonus"
        }
        
        msg = f"📜 *Transaction History* (Page {page+1}/{total_pages})\n━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for t in transactions:
            icon = type_icons.get(t['transaction_type'], "📌")
            type_name = type_names.get(t['transaction_type'], t['transaction_type'].replace('_', ' ').title())
            amount = float(t['amount'])
            balance_before = float(t['balance_before'])
            balance_after = float(t['balance_after'])
            
            if amount > 0:
                amount_display = f"+${amount:.2f}"
                arrow = "📈"
            else:
                amount_display = f"-${abs(amount):.2f}"
                arrow = "📉"
            
            transaction_time = format_timestamp_with_timezone(t['created_at'], user)
            
            msg += f"{icon} *{type_name}*\n"
            msg += f"┃ {arrow} Amount: *{amount_display}*\n"
            msg += f"┃ 💰 Balance: ${balance_before:.2f} → ${balance_after:.2f}\n"
            if t['description']:
                msg += f"┃ 📝 {t['description']}\n"
            msg += f"┃ 🕐 {transaction_time}\n"
            msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
            
            if len(msg) > 3500:
                if update.message:
                    await update.message.reply_text(msg, parse_mode="Markdown")
                else:
                    await update.callback_query.message.edit_text(msg, parse_mode="Markdown")
                msg = ""
        
        if msg:
            keyboard = build_tasks_pagination_keyboard(page, total_pages, "history")
            if update.message:
                await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=keyboard)
            else:
                await update.callback_query.message.edit_text(msg, parse_mode="Markdown", reply_markup=keyboard)
    
    except Exception as e:
        print(f"Error showing transaction history: {e}")
        if update.message:
            await update.message.reply_text("❌ Error loading transaction history.")
        else:
            await update.callback_query.message.edit_text("❌ Error loading transaction history.")
    finally:
        cur.close()
        conn.close()

async def show_tasks_page(update: Update, context: ContextTypes.DEFAULT_TYPE, uid, user, page):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT COUNT(*) as total FROM tasks WHERE assigned_to = %s AND status != 'available';", (uid,))
    total_count = cur.fetchone()["total"]
    if total_count == 0:
        if update.message:
            await update.message.reply_text("📭 *No Tasks Yet*\n\n🚀 Tap *Start Earning* to start earning!", parse_mode="Markdown")
        else:
            await update.callback_query.message.edit_text("📭 *No Tasks Yet*\n\n🚀 Tap *Start Earning* to start earning!", parse_mode="Markdown")
        cur.close()
        conn.close()
        return
    total_pages = (total_count + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    offset = page * ITEMS_PER_PAGE
    cur.execute("SELECT id, content, status, assigned_at FROM tasks WHERE assigned_to = %s AND status != 'available' ORDER BY assigned_at DESC LIMIT %s OFFSET %s;", (uid, ITEMS_PER_PAGE, offset))
    tasks = cur.fetchall()
    msg = f"📂 *My Tasks* (Page {page+1}/{total_pages})\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, t in enumerate(tasks, offset + 1):
        email = extract_email(t['content'])
        task_time = format_timestamp_with_timezone(t['assigned_at'], user)
        status_emoji = get_status_emoji(t['status'])
        status_text = get_status_text(t['status'])
        msg += f"┏━━ {i} ━━━━━━━━━━━━━━━\n┃ 📧 *Email:* `{email}`\n┃ {status_emoji} *Status:* {status_text}\n┃ 🕐 *Time:* {task_time}\n┗━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = build_tasks_pagination_keyboard(page, total_pages, "tasks")
    if update.message:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.callback_query.message.edit_text(msg, parse_mode="Markdown", reply_markup=keyboard)
    cur.close()
    conn.close()

def build_tasks_pagination_keyboard(current_page, total_pages, data_type="tasks"):
    keyboard = []
    nav_row = []
    if current_page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{data_type}_page:{current_page-1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {current_page+1}/{total_pages}", callback_data="noop"))
    if current_page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"{data_type}_page:{current_page+1}"))
    keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("🔙 Return", callback_data=f"back_to_{data_type}")])
    return InlineKeyboardMarkup(keyboard)

def get_status_emoji(status: str) -> str:
    status_emojis = {"assigned": "🟡", "submitted": "🟠", "approved": "🟢", "rejected": "🔴", "available": "📥"}
    return status_emojis.get(status, "⚪")

def get_status_text(status: str) -> str:
    status_texts = {"assigned": "In Progress", "submitted": "Pending Review", "approved": "Completed ✓", "rejected": "Rejected ✗", "available": "Available"}
    return status_texts.get(status, status.capitalize())

def extract_email(task_content: str) -> str:
    match = re.search(r"Email:\s*(.+)", task_content, re.IGNORECASE)
    return match.group(1).strip() if match else "N/A"

async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    
    if not is_admin(admin_id):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📂 Tasks Overview", callback_data="admin_tasks"), 
         InlineKeyboardButton("💰 User Balances", callback_data="admin_balances")],
        [InlineKeyboardButton("📥 Withdrawals", callback_data="admin_withdrawals"), 
         InlineKeyboardButton("👥 Users", callback_data="admin_users")],
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"), 
         InlineKeyboardButton("👤 User Info", callback_data="admin_userinfo_prompt")],
        [InlineKeyboardButton("🚫 Banned Users", callback_data="admin_banned"), 
         InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")]
    ]
    
    if is_super_admin(admin_id):
        keyboard.append([InlineKeyboardButton("👥 Manage Admins", callback_data="admin_admins")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    is_super = is_super_admin(admin_id)
    admin_username = get_admin_username(admin_id) or admin_id
    
    await update.message.reply_text(
        f"🛠 *ADMIN DASHBOARD*\n\n"
        f"👤 Admin: `{admin_username}`\n"
        f"🔰 Status: {'⭐ Super Admin' if is_super else '👤 Admin'}\n\n"
        f"Manage users, tasks & payments from here:\n"
        f"Select an option below:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    admin_id = str(query.from_user.id)
    if not is_admin(admin_id):
        await query.edit_message_text("❌ Unauthorized")
        return
    
    conn = get_conn()
    cur = conn.cursor()
    data = query.data
    
    if data == "admin_admins":
        if not is_super_admin(admin_id):
            await query.edit_message_text("❌ Only Super Admin can manage admins.")
            cur.close()
            conn.close()
            return
        
        await query.edit_message_text(
            "👥 *Admin Management*\n━━━━━━━━━━━━━━━━━━━━\n\n"
            "*Super Admin Commands:*\n\n"
            "📌 `/addadmin <user_id> <username>`\n"
            "   Add a new admin\n\n"
            "📌 `/removeadmin <user_id>`\n"
            "   Remove an admin\n\n"
            "📌 `/setapprovalgroup <admin_id> <group_id>`\n"
            "   Set approval group for admin\n\n"
            "📌 `/admins`\n"
            "   List all admins\n\n"
            "*Regular Admin Commands:*\n\n"
            "📌 `/setmyapprovalgroup <group_id>`\n"
            "   Set your own approval group\n\n"
            "📌 `/addtask_interactive`\n"
            "   Add a new task (interactive)\n\n"
            "📌 `/viewtasks`\n"
            "   View your tasks\n\n"
            "📌 `/deletetask <task_id>`\n"
            "   Delete a task",
            parse_mode="Markdown"
        )
        cur.close()
        conn.close()
        return
    
    if data == "admin_settings":
        await query.edit_message_text(
            "⚙️ *Settings Management*\n━━━━━━━━━━━━━━━━━━━━\n\n"
            "Use these commands to update bot settings:\n\n"
            "📌 `/setbotusername <username>`\n"
            "   Update bot's @username\n\n"
            "📌 `/setsupportbot <username>`\n"
            "   Update support bot @username\n\n"
            "📌 `/setadminid <user_id>`\n"
            "   Update admin user ID\n\n"
            "📌 `/setannounce <channel>`\n"
            "   Update announce channel\n\n"
            "📌 `/setwithdrawal <amount>`\n"
            "   Update minimum withdrawal\n\n"
            "📌 `/settaskpay <amount>`\n"
            "   Update task payment\n\n"
            "📌 `/setrefbonus <amount>`\n"
            "   Update referral bonus\n\n"
            "📌 `/setbotname <name>`\n"
            "   Update bot name\n\n"
            "📌 `/setdailybonus <amount>`\n"
            "   Update daily first task bonus\n\n"
            "📌 `/setusdkesrate <rate>`\n"
            "   Update USD to KES conversion rate\n\n"
            "📌 `/botsettings`\n"
            "   View all current settings",
            parse_mode="Markdown"
        )
        cur.close()
        conn.close()
        return
    
    if data == "admin_tasks":
        await admin_tasks_overview_callback(query, admin_id)
        cur.close()
        conn.close()
        return
    
    if data == "admin_balances":
        is_super = is_super_admin(admin_id)
        if is_super:
            cur.execute("SELECT user_id, COALESCE((data->>'available_balance')::numeric, 0) FROM users ORDER BY COALESCE((data->>'available_balance')::numeric, 0) DESC LIMIT 50;")
        else:
            cur.execute("SELECT user_id, COALESCE((data->>'available_balance')::numeric, 0) FROM users ORDER BY COALESCE((data->>'available_balance')::numeric, 0) DESC LIMIT 50;")
        rows = cur.fetchall()
        if not rows:
            await query.edit_message_text("💰 No users found.")
        else:
            message = "💰 *Top User Balances*\n\n"
            for user_id, balance in rows:
                line = f"👤 `{user_id}` → ${float(balance):.2f}\n"
                if len(message) + len(line) > MAX_MESSAGE_LENGTH:
                    await query.message.reply_text(message, parse_mode="Markdown")
                    message = ""
                message += line
            if message:
                await query.edit_message_text(message, parse_mode="Markdown")
        cur.close()
        conn.close()
        return
    
    if data == "admin_users":
        cur.execute("SELECT COUNT(*) FROM users;")
        total_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users WHERE data->>'banned' = 'true';")
        banned_users = cur.fetchone()[0]
        await query.edit_message_text(
            f"👥 *User Statistics*\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Total Users: *{total_users}*\n"
            f"🚫 Banned Users: *{banned_users}*\n"
            f"✅ Active Users: *{total_users - banned_users}*",
            parse_mode="Markdown"
        )
        cur.close()
        conn.close()
        return
    
    if data == "admin_withdrawals":
        cur.execute("SELECT COUNT(*) FROM withdrawals;")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending';")
        pending = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM withdrawals WHERE status='processed';")
        processed = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM withdrawals WHERE status='failed';")
        failed = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending_mpesa';")
        mpesa_pending = cur.fetchone()[0]
        await query.edit_message_text(
            f"📥 *Withdrawals Overview*\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💳 Total: *{total}*\n"
            f"⏳ Pending (Manual): *{pending}*\n"
            f"🔄 Pending (M-Pesa): *{mpesa_pending}*\n"
            f"✅ Processed: *{processed}*\n"
            f"❌ Failed: *{failed}*",
            parse_mode="Markdown"
        )
        cur.close()
        conn.close()
        return
    
    if data == "admin_stats":
        cur.execute("SELECT COUNT(*) FROM users;")
        users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM tasks;")
        tasks = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM withdrawals;")
        withdrawals = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM((data->>'available_balance')::numeric),0) FROM users;")
        total_balance = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM user_activities;")
        total_activities = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM transactions;")
        total_transactions = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM admin_settings;")
        total_admins = cur.fetchone()[0]
        await query.edit_message_text(
            f"📊 *System Statistics*\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Users: *{users}*\n"
            f"👤 Admins: *{total_admins}*\n"
            f"📂 Tasks: *{tasks}*\n"
            f"📥 Withdrawals: *{withdrawals}*\n"
            f"💰 Total User Balances: *${float(total_balance):.2f}*\n"
            f"📋 Total Activities: *{total_activities}*\n"
            f"📊 Total Transactions: *{total_transactions}*",
            parse_mode="Markdown"
        )
        cur.close()
        conn.close()
        return
    
    if data == "admin_broadcast":
        await query.edit_message_text(
            "📢 *Broadcast Commands*\n\n"
            "Use these commands to send broadcasts:\n\n"
            "🔹 `/broadcast <message>` - Send a custom message to all users\n"
            "🔹 `/earn` - Send earn message to users with zero balance\n\n"
            "⚠️ *Note:* Broadcasts have a 5-minute cooldown.",
            parse_mode="Markdown"
        )
        cur.close()
        conn.close()
        return
    
    if data == "admin_banned":
        conn2 = get_conn()
        cur2 = conn2.cursor(cursor_factory=RealDictCursor)
        try:
            cur2.execute("SELECT user_id, data FROM users WHERE data->>'banned' = 'true' ORDER BY user_id;")
            banned_users = cur2.fetchall()
            if not banned_users:
                await query.edit_message_text("✅ No banned users found.", parse_mode="Markdown")
            else:
                message = "🚫 *BANNED USERS LIST*\n━━━━━━━━━━━━━━━━━━━━\n\n"
                for user in banned_users:
                    user_data = user["data"]
                    banned_at = user_data.get("banned_at", 0)
                    banned_by = user_data.get("banned_by", "Unknown")
                    ban_date = datetime.fromtimestamp(banned_at).strftime("%Y-%m-%d %H:%M:%S") if banned_at else "Unknown"
                    message += f"👤 User ID: `{user['user_id']}`\n"
                    message += f"📅 Banned on: {ban_date}\n"
                    message += f"👮 Banned by: `{banned_by}`\n"
                    message += "━━━━━━━━━━━━━━━━━━━━\n\n"
                    if len(message) > 3500:
                        await query.message.reply_text(message, parse_mode="Markdown")
                        message = ""
                if message:
                    await query.edit_message_text(message, parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"❌ Error: {str(e)}", parse_mode="Markdown")
        finally:
            cur2.close()
            conn2.close()
        cur.close()
        conn.close()
        return
    
    if data == "admin_userinfo_prompt":
        await query.edit_message_text(
            "👤 *User Information*\n\n"
            "Please enter the user ID you want to check:\n\n"
            "Format: `/userinfo <user_id>`\n"
            "Example: `/userinfo 123456789`\n\n"
            "Or click below to return to dashboard:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_to_dashboard")]])
        )
        cur.close()
        conn.close()
        return
    
    if data == "back_to_dashboard":
        keyboard = [
            [InlineKeyboardButton("📂 Tasks Overview", callback_data="admin_tasks"), 
             InlineKeyboardButton("💰 User Balances", callback_data="admin_balances")],
            [InlineKeyboardButton("📥 Withdrawals", callback_data="admin_withdrawals"), 
             InlineKeyboardButton("👥 Users", callback_data="admin_users")],
            [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"), 
             InlineKeyboardButton("👤 User Info", callback_data="admin_userinfo_prompt")],
            [InlineKeyboardButton("🚫 Banned Users", callback_data="admin_banned"), 
             InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")]
        ]
        if is_super_admin(admin_id):
            keyboard.append([InlineKeyboardButton("👥 Manage Admins", callback_data="admin_admins")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        is_super = is_super_admin(admin_id)
        admin_username = get_admin_username(admin_id) or admin_id
        
        await query.edit_message_text(
            f"🛠 *ADMIN DASHBOARD*\n\n"
            f"👤 Admin: `{admin_username}`\n"
            f"🔰 Status: {'⭐ Super Admin' if is_super else '👤 Admin'}\n\n"
            f"Manage users, tasks & payments from here:\n"
            f"Select an option below:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        cur.close()
        conn.close()
        return
    
    cur.close()
    conn.close()
    await query.edit_message_text("⚠️ Unknown option.")

async def admin_tasks_overview_callback(query, admin_id):
    is_super = is_super_admin(admin_id)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if is_super:
            cur.execute("SELECT COUNT(*) as total, COUNT(CASE WHEN status = 'available' THEN 1 END) as available, COUNT(CASE WHEN status = 'assigned' THEN 1 END) as assigned, COUNT(CASE WHEN status = 'submitted' THEN 1 END) as submitted, COUNT(CASE WHEN status = 'approved' THEN 1 END) as approved, COUNT(CASE WHEN status = 'rejected' THEN 1 END) as rejected, COUNT(DISTINCT created_by) as admin_count FROM tasks;")
        else:
            cur.execute("SELECT COUNT(*) as total, COUNT(CASE WHEN status = 'available' THEN 1 END) as available, COUNT(CASE WHEN status = 'assigned' THEN 1 END) as assigned, COUNT(CASE WHEN status = 'submitted' THEN 1 END) as submitted, COUNT(CASE WHEN status = 'approved' THEN 1 END) as approved, COUNT(CASE WHEN status = 'rejected' THEN 1 END) as rejected, COUNT(DISTINCT created_by) as admin_count FROM tasks WHERE created_by = %s;", (admin_id,))
        
        stats = cur.fetchone()
        
        if is_super:
            cur.execute("SELECT created_by, COUNT(*) as task_count, COUNT(CASE WHEN status = 'available' THEN 1 END) as available FROM tasks GROUP BY created_by;")
            admin_stats = cur.fetchall()
            
            msg = f"📊 *All Tasks Overview*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        else:
            msg = f"📊 *Your Tasks Overview*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        
        msg += f"📂 Total Tasks: *{stats['total']}*\n"
        msg += f"📥 Available: *{stats['available']}*\n"
        msg += f"🟡 Assigned: *{stats['assigned']}*\n"
        msg += f"🟠 Pending Review: *{stats['submitted']}*\n"
        msg += f"✅ Approved: *{stats['approved']}*\n"
        msg += f"❌ Rejected: *{stats['rejected']}*\n"
        msg += f"👤 Admins with Tasks: *{stats['admin_count']}*\n\n"
        
        if is_super and admin_stats:
            msg += "━━━━━━━━━━━━━━━━━━━━\n"
            msg += "*📋 Tasks by Admin:*\n"
            msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
            for stat in admin_stats:
                admin_username = get_admin_username(stat['created_by']) or stat['created_by']
                msg += f"👤 Admin `{admin_username}`\n"
                msg += f"┣━━ Total: *{stat['task_count']}*\n"
                msg += f"┗━━ Available: *{stat['available']}*\n\n"
        
        keyboard = [[InlineKeyboardButton("📂 View All Tasks", callback_data="admin_view_tasks")]]
        keyboard.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_to_dashboard")])
        
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await query.edit_message_text(f"❌ Error: {str(e)}")
    finally:
        cur.close()
        conn.close()

async def admin_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ You are not allowed.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /userinfo <user_id>\nExample: /userinfo 123456789")
        return
    target_uid = context.args[0]
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT data FROM users WHERE user_id = %s;", (target_uid,))
        user_row = cur.fetchone()
        if not user_row:
            await update.message.reply_text(f"❌ User {target_uid} not found.")
            return
        user_data = user_row["data"]
        cur.execute("SELECT COUNT(*) FROM users WHERE data->>'referred_by' = %s;", (target_uid,))
        referrals_count = cur.fetchone()["count"]
        cur.execute("SELECT COUNT(*) as total_tasks, COUNT(CASE WHEN status = 'assigned' THEN 1 END) as assigned, COUNT(CASE WHEN status = 'submitted' THEN 1 END) as submitted, COUNT(CASE WHEN status = 'approved' THEN 1 END) as approved, COUNT(CASE WHEN status = 'rejected' THEN 1 END) as rejected FROM tasks WHERE assigned_to = %s;", (target_uid,))
        task_stats = cur.fetchone()
        cur.execute("SELECT COUNT(*) as total_withdrawals, COALESCE(SUM(amount), 0) as total_withdrawn, COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending_withdrawals, COUNT(CASE WHEN status = 'processed' THEN 1 END) as completed_withdrawals FROM withdrawals WHERE user_id = %s;", (target_uid,))
        withdrawal_stats = cur.fetchone()
        cur.execute("SELECT created_at, amount, final_amount, payment_method, status FROM withdrawals WHERE user_id = %s ORDER BY created_at DESC LIMIT 5;", (target_uid,))
        recent_withdrawals = cur.fetchall()
        
        cur.execute("SELECT COUNT(*) as total_transactions FROM transactions WHERE user_id = %s;", (target_uid,))
        transaction_count = cur.fetchone()["total_transactions"]
        
        cur.execute("SELECT COUNT(*) as total_activities FROM user_activities WHERE user_id = %s;", (target_uid,))
        activity_count = cur.fetchone()["total_activities"]
        
        affiliate_onhold = user_data.get("affiliate_onhold_balance", 0.0)
        withdrawal_onhold = user_data.get("withdrawal_onhold_balance", 0.0)
        referred_users_completed = user_data.get("referred_users_completed", [])
        
        last_task_date = user_data.get("last_task_date")
        today = datetime.now().strftime("%Y-%m-%d")
        daily_bonus_available = last_task_date != today
        
        locked_until = user_data.get("locked_until", 0)
        is_locked = locked_until > time.time()
        lock_status = f"🔒 Locked until {datetime.fromtimestamp(locked_until).strftime('%Y-%m-%d %H:%M:%S')}" if is_locked else "🔓 Active"
        timezone = user_data.get("timezone", DEFAULT_TIMEZONE)
        tz_display = format_timezone_simple(timezone)
        local_time = get_user_local_time(user_data).strftime("%Y-%m-%d %H:%M:%S")
        currency = user_data.get("currency", "USD")
        is_banned = user_data.get("banned", False)
        ban_status = "🚫 PERMANENTLY BANNED" if is_banned else "✅ Active"
        
        msg = f"👤 *USER DETAILS*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"🆔 *User ID:* `{target_uid}`\n"
        msg += f"📱 *Telegram ID:* `{target_uid}`\n"
        msg += f"🔐 *Status:* {lock_status}\n"
        msg += f"🚫 *Ban Status:* {ban_status}\n"
        msg += f"🌍 *Timezone:* {tz_display}\n"
        msg += f"⏰ *Local Time:* {local_time}\n"
        msg += f"💰 *Preferred Currency:* {currency}\n\n"
        msg += f"💵 *BALANCE INFORMATION*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"┣━━ Available: *${user_data.get('available_balance', 0):.2f}*\n"
        msg += f"┣━━ Task On-hold: *${user_data.get('onhold_balance', 0):.2f}*\n"
        msg += f"┣━━ Withdrawal On-Hold: *${withdrawal_onhold:.2f}*\n"
        msg += f"┗━━ Affiliate On-Hold: *${affiliate_onhold:.2f}*\n\n"
        msg += f"📊 *TASK STATISTICS*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"┣━━ Total Tasks: *{task_stats['total_tasks']}*\n"
        msg += f"┣━━ 🟡 In Progress: *{task_stats['assigned']}*\n"
        msg += f"┣━━ 🟠 Pending Review: *{task_stats['submitted']}*\n"
        msg += f"┣━━ 🟢 Completed: *{task_stats['approved']}*\n"
        msg += f"┗━━ 🔴 Rejected: *{task_stats['rejected']}*\n\n"
        msg += f"⚠️ *WARNINGS*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"┣━━ Rejection Warnings: *{user_data.get('warnings', 0)}/{MAX_REJECT_WARNINGS}*\n"
        msg += f"┣━━ Wrong Password Warnings: *{user_data.get('wrong_warnings', 0)}/{MAX_WRONG_WARNINGS}*\n"
        msg += f"┗━━ Total Warnings: *{user_data.get('total_warnings', 0)}/{MAX_TOTAL_WARNINGS}*\n\n"
        msg += f"⭐ *DAILY BONUS*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"┣━━ Last Task Date: *{last_task_date or 'Never'}*\n"
        msg += f"┗━━ Daily Bonus Available: *{'✅ Yes' if daily_bonus_available else '❌ No (claimed today)'}*\n\n"
        msg += f"👥 *REFERRAL INFO*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"┣━━ Referred By: `{user_data.get('referred_by', 'None')}`\n"
        msg += f"┣━━ Total Referrals: *{referrals_count}*\n"
        msg += f"┗━━ Completed First Task: *{len(referred_users_completed)}*\n\n"
        msg += f"💳 *WITHDRAWAL SUMMARY*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"┣━━ Total Withdrawals: *{withdrawal_stats['total_withdrawals']}*\n"
        msg += f"┣━━ Total Withdrawn: *${withdrawal_stats['total_withdrawn']:.2f}*\n"
        msg += f"┣━━ ⏳ Pending: *{withdrawal_stats['pending_withdrawals']}*\n"
        msg += f"┗━━ ✅ Completed: *{withdrawal_stats['completed_withdrawals']}*\n\n"
        msg += f"📋 *ACTIVITIES & TRANSACTIONS*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"┣━━ Total Activities: *{activity_count}*\n"
        msg += f"┗━━ Total Transactions: *{transaction_count}*\n\n"
        
        if recent_withdrawals:
            msg += f"📋 *RECENT WITHDRAWALS (Last 5)*\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for w in recent_withdrawals:
                status_emoji = "🟡" if w['status'] == 'pending' else "🟢" if w['status'] == 'processed' else "🔴"
                date = w['created_at'].strftime("%Y-%m-%d %H:%M")
                msg += f"┣━━ {status_emoji} ${float(w['amount']):.2f} "
                if w['final_amount']:
                    msg += f"(Rec: ${float(w['final_amount']):.2f}) "
                msg += f"- {w['payment_method']}\n┃    🕐 {date}\n"
        
        msg += f"\n⚙️ *ACCOUNT SETTINGS*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        last_task = user_data.get('last_task_time', 0)
        last_task_time = datetime.fromtimestamp(last_task).strftime('%Y-%m-%d %H:%M:%S') if last_task else 'Never'
        msg += f"┣━━ Last Task: {last_task_time}\n"
        msg += f"┣━━ Tasks Completed: *{user_data.get('tasks_completed', 0)}*\n"
        msg += f"┗━━ Active Task ID: `{user_data.get('active_task_id', 'None')}`\n"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
        
    except Exception as e:
        print(f"Error in admin_userinfo: {e}")
        await update.message.reply_text(f"❌ Error fetching user info: {str(e)}")
    finally:
        cur.close()
        conn.close()

async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ You are not allowed.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /ban <user_id>\nExample: /ban 123456789")
        return
    uid = context.args[0]
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
        row = cur.fetchone()
        if not row:
            await update.message.reply_text(f"❌ User {uid} not found.")
            return
        user = row["data"]
        user["banned"] = True
        user["banned_at"] = time.time()
        user["banned_by"] = admin_id
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        log_activity(uid, "admin_ban", f"User banned by admin {admin_id}", {"banned_by": admin_id})
        try:
            await context.bot.send_message(chat_id=int(uid), text="🚫 *You have been banned from using this bot!*\n\nReason: Violation of bot rules.\n\nIf you believe this is a mistake, please contact support.", parse_mode="Markdown")
        except Exception as e:
            print(f"Could not notify banned user {uid}: {e}")
        await update.message.reply_text(f"✅ User `{uid}` has been banned successfully.", parse_mode="Markdown")
    except Exception as e:
        print(f"Error banning user: {e}")
        await update.message.reply_text(f"❌ Error banning user: {str(e)}")
    finally:
        cur.close()
        conn.close()

async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ You are not allowed.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /unban <user_id>\nExample: /unban 123456789")
        return
    uid = context.args[0]
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
        row = cur.fetchone()
        if not row:
            await update.message.reply_text(f"❌ User {uid} not found.")
            return
        user = row["data"]
        user["banned"] = False
        if "banned_at" in user:
            del user["banned_at"]
        if "banned_by" in user:
            del user["banned_by"]
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        log_activity(uid, "admin_unban", f"User unbanned by admin {admin_id}", {"unbanned_by": admin_id})
        try:
            await context.bot.send_message(chat_id=int(uid), text="✅ *You have been unbanned!*\n\nYou can now use the bot again.\n\nPlease follow the rules to avoid being banned again.", parse_mode="Markdown")
        except Exception as e:
            print(f"Could not notify unbanned user {uid}: {e}")
        await update.message.reply_text(f"✅ User `{uid}` has been unbanned successfully.", parse_mode="Markdown")
    except Exception as e:
        print(f"Error unbanning user: {e}")
        await update.message.reply_text(f"❌ Error unbanning user: {str(e)}")
    finally:
        cur.close()
        conn.close()

async def admin_banned_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ You are not allowed.")
        return
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT user_id, data FROM users WHERE data->>'banned' = 'true' ORDER BY user_id;")
        banned_users = cur.fetchall()
        if not banned_users:
            await update.message.reply_text("✅ No banned users found.")
            return
        message = "🚫 *BANNED USERS LIST*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for user in banned_users:
            user_data = user["data"]
            banned_at = user_data.get("banned_at", 0)
            banned_by = user_data.get("banned_by", "Unknown")
            ban_date = datetime.fromtimestamp(banned_at).strftime("%Y-%m-%d %H:%M:%S") if banned_at else "Unknown"
            message += f"👤 User ID: `{user['user_id']}`\n"
            message += f"📅 Banned on: {ban_date}\n"
            message += f"👮 Banned by: `{banned_by}`\n"
            message += f"💰 Balance: ${user_data.get('available_balance', 0):.2f}\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n\n"
            if len(message) > 3500:
                await update.message.reply_text(message, parse_mode="Markdown")
                message = ""
        if message:
            await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        print(f"Error listing banned users: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
    finally:
        cur.close()
        conn.close()

async def admin_set_bot_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /setbotusername <username>\n"
            "Example: /setbotusername earnmibot\n\n"
            "Current value: @" + get_bot_username()
        )
        return
    
    username = context.args[0].replace('@', '').strip()
    if not username:
        await update.message.reply_text("❌ Invalid username")
        return
    
    if update_bot_username(username):
        await update.message.reply_text(
            f"✅ Bot username updated to: @{username}\n"
            f"All referral links will now use @{username}"
        )
    else:
        await update.message.reply_text("❌ Failed to update bot username")

async def admin_set_support_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /setsupportbot <username>\n"
            "Example: /setsupportbot gmsupportdeskbot\n\n"
            "Current value: @" + get_support_bot()
        )
        return
    
    support = context.args[0].replace('@', '').strip()
    if not support:
        await update.message.reply_text("❌ Invalid username")
        return
    
    if update_support_bot(support):
        await update.message.reply_text(
            f"✅ Support bot updated to: @{support}\n"
            f"All support links will now use @{support}"
        )
    else:
        await update.message.reply_text("❌ Failed to update support bot")

async def admin_set_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_super_admin(admin_id):
        await update.message.reply_text("❌ Only Super Admin can change the admin ID.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /setadminid <user_id>\n"
            "Example: /setadminid 0\n\n"
            "Current value: " + get_admin_id()
        )
        return
    
    new_admin_id = context.args[0].strip()
    if not new_admin_id.isdigit():
        await update.message.reply_text("❌ Invalid user ID. Must be a number.")
        return
    
    if update_admin_id(new_admin_id):
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO admin_settings (admin_id, username, approval_group_id, is_super_admin) VALUES (%s, %s, %s, TRUE) "
                "ON CONFLICT (admin_id) DO UPDATE SET is_super_admin = TRUE, updated_at = CURRENT_TIMESTAMP",
                (new_admin_id, "SuperAdmin", DEFAULT_APPROVAL_GROUP_ID)
            )
            conn.commit()
        except Exception as e:
            print(f"Error updating super admin in settings: {e}")
        finally:
            cur.close()
            conn.close()
        
        await update.message.reply_text(
            f"✅ Admin ID updated to: {new_admin_id}\n"
            f"⚠️ You may need to restart the bot for changes to take full effect."
        )
    else:
        await update.message.reply_text("❌ Failed to update admin ID")

async def admin_set_announce_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /setannounce <channel>\n"
            "Example: /setannounce +Cbt7zJSZLeZiNzE0\n\n"
            "Current value: " + get_announce_channel()
        )
        return
    
    channel = context.args[0].strip()
    if not channel:
        await update.message.reply_text("❌ Invalid channel")
        return
    
    if update_announce_channel(channel):
        await update.message.reply_text(
            f"✅ Announce channel updated to: {channel}\n"
            f"All invite links will now use this channel"
        )
    else:
        await update.message.reply_text("❌ Failed to update announce channel")

async def admin_set_referral_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /setrefbonus <amount>\n"
            "Example: /setrefbonus 0.05\n\n"
            "Current value: ${:.2f}".format(get_referral_bonus())
        )
        return
    
    try:
        amount = float(context.args[0])
        if amount < 0:
            await update.message.reply_text("❌ Amount must be positive")
            return
        
        if update_referral_bonus(amount):
            await update.message.reply_text(
                f"✅ Referral bonus updated to ${amount:.2f}\n"
                f"🎁 Users will now earn ${amount:.2f} per referral (released after first task)"
            )
        else:
            await update.message.reply_text("❌ Failed to update referral bonus")
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number")

async def admin_set_bot_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /setbotname <name>\n"
            "Example: /setbotname Gmail Farming\n\n"
            "Current value: " + get_bot_name()
        )
        return
    
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("❌ Invalid name")
        return
    
    if update_bot_name(name):
        await update.message.reply_text(
            f"✅ Bot name updated to: {name}\n"
            f"All messages will now use '{name}' as the bot name"
        )
    else:
        await update.message.reply_text("❌ Failed to update bot name")

async def admin_set_daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /setdailybonus <amount>\n"
            "Example: /setdailybonus 0.05\n\n"
            "Current value: ${:.2f}".format(get_daily_bonus())
        )
        return
    
    try:
        amount = float(context.args[0])
        if amount < 0:
            await update.message.reply_text("❌ Amount must be positive")
            return
        
        if update_daily_bonus(amount):
            await update.message.reply_text(
                f"✅ Daily bonus updated to ${amount:.2f}\n"
                f"🌟 Users will earn ${amount:.2f} for their first approved task each day!"
            )
        else:
            await update.message.reply_text("❌ Failed to update daily bonus")
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number")

async def admin_set_usd_kes_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /setusdkesrate <rate>\n"
            "Example: /setusdkesrate 150.0\n\n"
            "Current value: {:.2f}".format(get_usd_to_kes_rate())
        )
        return
    
    try:
        rate = float(context.args[0])
        if rate <= 0:
            await update.message.reply_text("❌ Rate must be greater than 0")
            return
        
        if update_usd_to_kes_rate(rate):
            await update.message.reply_text(
                f"✅ USD to KES rate updated to {rate:.2f}\n"
                f"💰 M-Pesa withdrawals will use this rate for conversion"
            )
        else:
            await update.message.reply_text("❌ Failed to update USD to KES rate")
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number")

async def admin_set_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /setwithdrawal <amount>\n"
            "Example: /setwithdrawal 1.00\n\n"
            "Current value: ${:.2f}".format(get_withdrawal_limit())
        )
        return
    
    try:
        amount = float(context.args[0])
        if amount < 0:
            await update.message.reply_text("❌ Amount must be positive")
            return
        
        if update_setting('withdrawal_limit', str(amount)):
            await update.message.reply_text(
                f"✅ Minimum withdrawal updated to ${amount:.2f}\n"
                f"💰 New withdrawals will require at least ${amount:.2f}"
            )
        else:
            await update.message.reply_text("❌ Failed to update withdrawal limit")
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number")

async def admin_set_taskpay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /settaskpay <amount>\n"
            "Example: /settaskpay 0.15\n\n"
            "Current value: ${:.2f}".format(get_task_pay())
        )
        return
    
    try:
        amount = float(context.args[0])
        if amount < 0:
            await update.message.reply_text("❌ Amount must be positive")
            return
        
        if update_setting('task_pay', str(amount)):
            await update.message.reply_text(
                f"✅ Task payment updated to ${amount:.2f}\n"
                f"💰 Users will earn ${amount:.2f} per completed task"
            )
        else:
            await update.message.reply_text("❌ Failed to update task payment")
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number")


async def admin_setup_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Only admins")
        return
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
    row = cur.fetchone()
    user = row["data"] if row else DEFAULT_USER.copy()
    user["setup_in_progress"] = True
    user["setup_step"] = 0
    cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
    conn.commit()
    cur.close()
    conn.close()
    await update.message.reply_text(f"Setup Wizard Started Step 1/{len(SETUP_KEYS_ORDER)}: Send your {SETUP_KEYS_ORDER[0][1]}")

async def admin_cancel_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
    row = cur.fetchone()
    if row:
        user = row["data"]
        user["setup_in_progress"] = False
        user["setup_step"] = 0
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
    cur.close()
    conn.close()
    await update.message.reply_text("Setup cancelled")

async def admin_set_approval_group_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Unauthorized")
        return
    if not context.args:
        await update.message.reply_text(f"Usage: /setapprovalgroup -1001234567890 Current: {get_admin_approval_group(uid) or 'Not set'}")
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
        row = cur.fetchone()
        if row:
            user = row["data"]
            user["awaiting_approval_group"] = True
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
        cur.close()
        conn.close()
        return
    try:
        group_id = int(context.args[0])
        set_admin_approval_group(uid, group_id)
        update_setting('approval_group_id', str(group_id))
        await update.message.reply_text(f"Approval Group updated to {group_id}")
    except ValueError:
        await update.message.reply_text("Invalid group ID")

async def admin_settings_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("Unauthorized")
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM bot_settings ORDER BY key;")
    settings = cur.fetchall()
    cur.execute("SELECT admin_id, username, approval_group_id, is_super_admin FROM admin_settings ORDER BY is_super_admin DESC;")
    admins = cur.fetchall()
    cur.close()
    conn.close()
    msg = "BOT SETTINGS\n"
    for k,v in settings:
        msg += f"{k}: {v}\n"
    msg += "\nADMINS\n"
    for admin_id, username, appr, is_super in admins:
        star = "SUPER" if is_super else "Admin"
        msg += f"{star} {admin_id} @{username or ''} Group: {appr or 'not set'}\n"
    msg += "\nCommands: /setapprovalgroup, /settaskpay, /setwithdrawal, /setbotusername, /setannounce, /setsupportbot, /setbotname, /setup, /broadcast"
    await update.message.reply_text(msg)

async def admin_show_all_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT key, value, updated_at FROM bot_settings ORDER BY key;")
        settings = cur.fetchall()
        
        message = "⚙️ *ALL BOT SETTINGS*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for key, value, updated_at in settings:
            display_key = key.replace('_', ' ').title()
            if key in ['bot_username', 'support_bot']:
                display_value = f"@{value}"
            elif key in ['withdrawal_limit', 'task_pay', 'referral_bonus', 'daily_bonus']:
                display_value = f"${value}"
            elif key == 'usd_to_kes_rate':
                display_value = f"{float(value):.2f} KES/USD"
            else:
                display_value = value
            message += f"📌 *{display_key}:*\n"
            message += f"┣━━ Value: `{display_value}`\n"
            if updated_at:
                message += f"┗━━ Updated: {updated_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            message += "\n"
        
        cur.execute("SELECT admin_id, username, approval_group_id, is_super_admin FROM admin_settings ORDER BY is_super_admin DESC;")
        admins = cur.fetchall()
        
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        message += "👥 *ADMIN SETTINGS*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for admin in admins:
            status = "⭐ SUPER" if admin[3] else "👤 Admin"
            username = admin[1] or admin[0]
            message += f"🆔 ID: `{admin[0]}`\n"
            message += f"📛 Username: `{username}`\n"
            message += f"📢 Approval Group: `{admin[2]}`\n"
            message += f"🔰 Status: {status}\n\n"
        
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        message += "🛠 *Update Commands:*\n"
        message += "• `/setbotusername <username>`\n"
        message += "• `/setsupportbot <username>`\n"
        message += "• `/setadminid <user_id>` (Super Admin only)\n"
        message += "• `/setannounce <channel>`\n"
        message += "• `/setwithdrawal <amount>`\n"
        message += "• `/settaskpay <amount>`\n"
        message += "• `/setrefbonus <amount>`\n"
        message += "• `/setbotname <name>`\n"
        message += "• `/setdailybonus <amount>`\n"
        message += "• `/setusdkesrate <rate>`\n"
        message += "• `/addadmin <user_id> <username>` (Super Admin only)\n"
        message += "• `/removeadmin <user_id>` (Super Admin only)\n"
        message += "• `/setapprovalgroup <admin_id> <group_id>` (Super Admin only)"
        
        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    finally:
        cur.close()
        conn.close()

async def admin_reduce_onhold_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /reduceonhold <user_id> <amount>\n\nThis command reduces the withdrawal on-hold balance of a user.")
        return
    
    uid = context.args[0]
    try:
        amount = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid amount")
        return
    
    if amount <= 0:
        await update.message.reply_text("❌ Amount must be greater than 0")
        return
    
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
        row = cur.fetchone()
        if not row:
            await update.message.reply_text("❌ User not found")
            return
        
        user = row["data"]
        current_onhold = user.get("withdrawal_onhold_balance", 0.0)
        
        if current_onhold < amount:
            await update.message.reply_text(f"❌ User's withdrawal on-hold balance is ${current_onhold:.2f}, cannot reduce by ${amount:.2f}")
            return
        
        user["withdrawal_onhold_balance"] = current_onhold - amount
        
        log_transaction(
            uid,
            "admin_reduce_onhold",
            -amount,
            current_onhold,
            user["withdrawal_onhold_balance"],
            f"Admin reduced withdrawal on-hold balance by ${amount:.2f}",
            admin_id
        )
        
        cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
        conn.commit()
        
        log_activity(uid, "admin_reduce_onhold", f"Admin reduced withdrawal on-hold balance by ${amount:.2f}", {"amount": amount})
        
        await update.message.reply_text(
            f"✅ Reduced withdrawal on-hold balance for user `{uid}` by ${amount:.2f}\n\n"
            f"New withdrawal on-hold balance: ${user['withdrawal_onhold_balance']:.2f}",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    finally:
        cur.close()
        conn.close()

async def admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /message <user_id> <text>")
        return
    
    uid = context.args[0]
    msg = " ".join(context.args[1:])
    try:
        await context.bot.send_message(uid, msg)
        await update.message.reply_text("✅ Message sent")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send message: {str(e)}")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users;")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM tasks;")
    total_tasks = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM withdrawals;")
    total_withdrawals = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE data->>'banned' = 'true';")
    banned_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM user_activities;")
    total_activities = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM transactions;")
    total_transactions = cur.fetchone()[0]
    cur.close()
    conn.close()
    await update.message.reply_text(
        f"📊 *Admin Statistics*\n\n"
        f"👤 Total Users: {total_users}\n"
        f"🚫 Banned Users: {banned_users}\n"
        f"📂 Total Tasks: {total_tasks}\n"
        f"🏧 Total Withdrawals: {total_withdrawals}\n"
        f"📋 Total Activities: {total_activities}\n"
        f"📊 Total Transactions: {total_transactions}"
    )

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users;")
    total_users = cur.fetchone()[0]
    cur.close()
    conn.close()
    await update.message.reply_text(f"👥 Total Users: {total_users}")

async def admin_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        return
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, user_id, amount, status FROM withdrawals WHERE status = 'pending' OR status = 'pending_mpesa';")
    pending = cur.fetchall()
    if not pending:
        await update.message.reply_text("✅ No pending withdrawals")
        return
    msg = "🏧 Pending Withdrawals\n\n"
    for w in pending:
        status_icon = "🔄" if w['status'] == 'pending_mpesa' else "⏳"
        msg += f"{status_icon} ID: {w['id']} | User: {w['user_id']} | Amount: ${w['amount']:.2f}\n"
    await update.message.reply_text(msg)
    cur.close()
    conn.close()

async def admin_balances(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        return
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT user_id, data FROM users ORDER BY (data->>'available_balance')::numeric DESC LIMIT 50;")
    users = cur.fetchall()
    if not users:
        await update.message.reply_text("💰 No users found.")
        return
    msg = "💰 Top User Balances\n\n"
    for u in users:
        balance = u["data"].get("available_balance", 0)
        msg += f"{u['user_id']}: ${balance:.2f}\n"
        if len(msg) > MAX_MESSAGE_LENGTH:
            await update.message.reply_text(msg)
            msg = ""
    if msg:
        await update.message.reply_text(msg)
    cur.close()
    conn.close()

async def admin_addbalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addbalance <user_id> <amount>")
        return
    uid = context.args[0]
    try:
        amount = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid amount")
        return
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text("❌ User not found")
        return
    user = row["data"]
    balance_before = user.get("available_balance", 0.0)
    user["available_balance"] = balance_before + amount
    
    log_transaction(
        uid,
        "admin_add",
        amount,
        balance_before,
        user["available_balance"],
        f"Admin added ${amount} to balance",
        admin_id
    )
    
    cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
    conn.commit()
    log_activity(uid, "admin_balance_add", f"Admin added ${amount} to balance", {"amount": amount})
    cur.close()
    conn.close()
    await update.message.reply_text(f"✅ ${amount:.2f} added successfully to user {uid}")

async def admin_reducebalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /reducebalance <user_id> <amount>")
        return
    uid = context.args[0]
    try:
        amount = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid amount")
        return
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text("❌ User not found")
        return
    user = row["data"]
    balance_before = user.get("available_balance", 0.0)
    user["available_balance"] = max(0, balance_before - amount)
    
    log_transaction(
        uid,
        "admin_reduce",
        -amount,
        balance_before,
        user["available_balance"],
        f"Admin reduced ${amount} from balance",
        admin_id
    )
    
    cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
    conn.commit()
    log_activity(uid, "admin_balance_reduce", f"Admin reduced ${amount} from balance", {"amount": amount})
    cur.close()
    conn.close()
    await update.message.reply_text(f"➖ ${amount:.2f} reduced successfully from user {uid}")

async def admin_fix_stuck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Admin only command.")
        return
    
    try:
        conn = get_conn()
        cur = conn.cursor()
        now = int(time.time())
        cur.execute("SELECT id, assigned_to FROM tasks WHERE status = 'assigned' AND assigned_at IS NOT NULL AND assigned_at < (EXTRACT(EPOCH FROM NOW()) - %s)", (TASK_TIMEOUT_SECONDS,))
        stuck_tasks = cur.fetchall()
        task_ids = [row[0] for row in stuck_tasks]
        affected_users = list(set(row[1] for row in stuck_tasks if row[1]))
        if task_ids:
            cur.execute("UPDATE tasks SET assigned_to = NULL, status = 'available', assigned_at = NULL WHERE id = ANY(%s)", (task_ids,))
            for uid in affected_users:
                cur.execute("UPDATE users SET data = jsonb_set(data, '{active_task_id}', 'null'::jsonb, true) WHERE user_id = %s", (uid,))
        twenty_four_hours_ago = now - (MAX_TASK_AGE_HOURS * 60 * 60)
        cur.execute("SELECT id, assigned_to, status FROM tasks WHERE assigned_at < %s AND status NOT IN ('approved', 'submitted')", (twenty_four_hours_ago,))
        old_tasks = cur.fetchall()
        old_task_ids = [row[0] for row in old_tasks]
        old_affected_users = list(set(row[1] for row in old_tasks if row[1] and row[1] not in affected_users))
        if old_task_ids:
            for task_id, uid, status in old_tasks:
                if status == 'assigned' and uid:
                    cur.execute("UPDATE users SET data = jsonb_set(data, '{active_task_id}', 'null'::jsonb, true) WHERE user_id = %s AND (data->>'active_task_id')::text = %s", (uid, str(task_id)))
            cur.execute("DELETE FROM tasks WHERE id = ANY(%s)", (old_task_ids,))
        conn.commit()
        all_affected = set(affected_users + old_affected_users)
        for uid in all_affected:
            try:
                await context.bot.send_message(chat_id=int(uid), text="⏰ *Task Update*\n\nYour expired tasks have been automatically cleaned up.\nYou can now request new tasks!", parse_mode="Markdown")
            except Exception as e:
                print(f"Failed to notify user {uid}: {e}")
        response = f"♻️ Fixed {len(task_ids)} expired task(s) (returned to pool).\n🗑️ Deleted {len(old_task_ids)} old task(s) (older than 24 hours).\n👤 {len(all_affected)} user(s) notified."
        await update.message.reply_text(response)
    except Exception as e:
        print("ERROR in admin_fix_stuck:", e)
        await update.message.reply_text("⚠️ Error fixing stuck tasks.")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

async def return_stuck_tasks(context: ContextTypes.DEFAULT_TYPE):
    print("🔄 Running automated stuck task checker...")
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        now = int(time.time())
        cur.execute("UPDATE users SET data = jsonb_set(jsonb_set(data, '{locked_until}', '0'::jsonb, true), '{warnings}', '0'::jsonb, true) WHERE data ? 'locked_until' AND (data->>'locked_until')::bigint > 0 AND %s >= (data->>'locked_until')::bigint", (now,))
        print("🔓 Expired user locks cleared and warnings reset:", cur.rowcount)
        cur.execute("SELECT id, assigned_to FROM tasks WHERE status = 'assigned' AND assigned_at IS NOT NULL AND %s >= assigned_at + %s", (now, TASK_TIMEOUT_SECONDS))
        expired_tasks = cur.fetchall()
        print("📌 Expired tasks found:", len(expired_tasks))
        for task_id, uid in expired_tasks:
            print(f"♻️ Returning task {task_id} from user {uid}")
            cur.execute("UPDATE tasks SET status = 'available', assigned_to = NULL, assigned_at = NULL WHERE id = %s", (task_id,))
            if uid:
                cur.execute("UPDATE users SET data = jsonb_set(data, '{active_task_id}', 'null'::jsonb, true) WHERE user_id = %s", (uid,))
                try:
                    await context.bot.send_message(chat_id=uid, text="⏱ *Task Expired*\n\nYou took too long to submit the task (2 hours).\n📌 The task has been returned to the pool.\n\nYou can request a new task anytime.", parse_mode="Markdown")
                except Exception as e:
                    print(f"⚠️ Failed to notify user {uid}: {e}")
        twenty_four_hours_ago = now - (MAX_TASK_AGE_HOURS * 60 * 60)
        cur.execute("SELECT id, assigned_to, status FROM tasks WHERE assigned_at < %s AND status NOT IN ('approved', 'submitted')", (twenty_four_hours_ago,))
        old_tasks = cur.fetchall()
        print("🗑️ Old tasks to delete (24h+):", len(old_tasks))
        for task_id, uid, status in old_tasks:
            if uid and status == 'assigned':
                cur.execute("UPDATE users SET data = jsonb_set(data, '{active_task_id}', 'null'::jsonb, true) WHERE user_id = %s", (uid,))
                try:
                    await context.bot.send_message(chat_id=uid, text="⏰ *Task Removed*\n\nYour task has been automatically removed after 24 hours.\nYou can request a new task anytime.", parse_mode="Markdown")
                except Exception as e:
                    print(f"⚠️ Failed to notify user {uid}: {e}")
        if old_tasks:
            cur.execute("DELETE FROM tasks WHERE id = ANY(%s)", ([t[0] for t in old_tasks],))
        conn.commit()
        print("✅ Automated stuck task cycle completed")
    except Exception as e:
        print("❌ AUTOMATED STUCK TASK ERROR:", e)
        if conn:
            conn.rollback()
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

async def admin_warning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /warning <user_id>")
        return
    uid = context.args[0]
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text("❌ User not found")
        return
    user = row["data"]
    user["warnings"] = user.get("warnings", 0) + 1
    user["total_warnings"] = user.get("total_warnings", 0) + 1
    total_warnings = user.get("total_warnings", 0)
    
    if total_warnings >= MAX_TOTAL_WARNINGS:
        user["banned"] = True
        user["banned_at"] = time.time()
        user["banned_by"] = admin_id
        await context.bot.send_message(uid, "🔒 *PERMANENT BAN*\n\nYou have been permanently banned for reaching 15 total warnings.\n\nThis action cannot be undone.", parse_mode="Markdown")
        await update.message.reply_text(f"⚠️ User {uid} has been AUTO-BANNED (15 total warnings)")
    else:
        await context.bot.send_message(uid, f"⚠️ Warning {user['warnings']}/3\n📊 Total Warnings: {total_warnings}/{MAX_TOTAL_WARNINGS}")
        await update.message.reply_text(f"⚠️ Warning issued to {uid}\nTotal Warnings: {total_warnings}/{MAX_TOTAL_WARNINGS}")
    
    cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
    conn.commit()
    cur.close()
    conn.close()

async def admin_fix_mpesa_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text(
            "Usage:\n"
            "/fixmpesa <withdrawal_id> refund\n"
            "/fixmpesa <withdrawal_id> confirm <transaction_id>\n"
            "/fixmpesa all refund_stuck\n\n"
            "Examples:\n"
            "/fixmpesa 123 refund\n"
            "/fixmpesa 123 confirm MPESA123456\n"
            "/fixmpesa all refund_stuck - fixes all stuck on-hold balances"
        )
        return
    
    withdrawal_id = context.args[0]
    
    # Special command: fix all stuck
    if withdrawal_id.lower() == "all" and len(context.args) >= 2 and context.args[1].lower() in ["refund_stuck", "refund", "fix"]:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute("SELECT * FROM withdrawals WHERE status IN ('pending_mpesa', 'failed') ORDER BY created_at DESC LIMIT 100")
            all_w = cur.fetchall()
            fixed = 0
            details = []
            for w in all_w:
                wid = w['id']
                uid = w['user_id']
                amount = float(w['amount'])
                status = w['status']
                cur.execute("SELECT data FROM users WHERE user_id = %s", (uid,))
                row = cur.fetchone()
                if not row:
                    continue
                user_data = row['data']
                onhold = float(user_data.get('withdrawal_onhold_balance', 0) or 0)
                available = float(user_data.get('available_balance', 0) or 0)
                # If on-hold contains this amount, it's stuck
                if onhold >= 0.01 and (status == 'failed' or status == 'pending_mpesa'):
                    # For pending, check if older than 2 mins or has failed callback
                    should_refund = False
                    if status == 'failed' and onhold >= amount - 0.5:
                        should_refund = True
                    elif status == 'pending_mpesa':
                        # Check age or callback
                        should_refund = True
                        # Try to find failed callback
                        conv_id = w.get('mpesa_conversation_id')
                        if conv_id:
                            cur.execute("SELECT result_code FROM mpesa_callbacks WHERE conversation_id = %s ORDER BY received_at DESC LIMIT 1", (conv_id,))
                            cb = cur.fetchone()
                            if cb and str(cb.get('result_code')) == '0':
                                should_refund = False  # Don't refund success
                
                    if should_refund and onhold > 0:
                        refund_amount = min(amount, onhold) if status == 'failed' else amount
                        user_data['withdrawal_onhold_balance'] = max(0, onhold - refund_amount)
                        user_data['available_balance'] = available + refund_amount
                        cur.execute("UPDATE users SET data = %s WHERE user_id = %s", (Json(user_data), uid))
                        cur.execute("UPDATE withdrawals SET status='failed', mpesa_result=%s, processed_at=CURRENT_TIMESTAMP WHERE id=%s", (f"Fixed by admin {admin_id} - refund_stuck", wid))
                        fixed += 1
                        details.append(f"#{wid} user {uid} ${refund_amount}")
            conn.commit()
            await update.message.reply_text(f"✅ Fixed {fixed} stuck withdrawals:\n" + "\n".join(details[:20]))
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}\n{traceback.format_exc()}")
        finally:
            cur.close()
            conn.close()
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Need action: refund or confirm")
        return
    
    action = context.args[1].lower()
    
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Allow fixing both pending_mpesa and failed (stuck)
        cur.execute("SELECT * FROM withdrawals WHERE id = %s;", (withdrawal_id,))
        w = cur.fetchone()
        
        if not w:
            await update.message.reply_text(f"❌ Withdrawal #{withdrawal_id} not found.")
            cur.close()
            conn.close()
            return
        
        if action == "refund":
            # Use new refund function that handles stuck on-hold
            success = refund_mpesa_withdrawal_withdrawal(w, f"Manually refunded by admin {admin_id}", "ADMIN_REFUND")
            
            if success:
                await update.message.reply_text(f"✅ Withdrawal #{withdrawal_id} refunded successfully. Balance restored.")
            else:
                await update.message.reply_text(f"❌ Failed to refund withdrawal #{withdrawal_id}.")
        
        elif action == "confirm" and len(context.args) >= 3:
            transaction_id = context.args[2]
            # Confirm - remove from on-hold without refunding available
            cur.execute("SELECT data FROM users WHERE user_id = %s", (w['user_id'],))
            user_row = cur.fetchone()
            if user_row:
                user_data = user_row['data']
                amount = float(w['amount'])
                onhold = float(user_data.get('withdrawal_onhold_balance', 0) or 0)
                user_data['withdrawal_onhold_balance'] = max(0, onhold - amount)
                cur.execute("UPDATE users SET data = %s WHERE user_id = %s", (Json(user_data), w['user_id']))
                cur.execute("UPDATE withdrawals SET status='processed', mpesa_transaction_id=%s, mpesa_result=%s, processed_at=CURRENT_TIMESTAMP WHERE id=%s", (transaction_id, f"Manually confirmed by admin {admin_id}", w['id']))
                conn.commit()
                await update.message.reply_text(f"✅ Withdrawal #{withdrawal_id} confirmed as processed.")
            else:
                await update.message.reply_text(f"❌ User not found for withdrawal #{withdrawal_id}.")
        
        else:
            await update.message.reply_text("❌ Invalid action. Use 'refund' or 'confirm <transaction_id>'")
    
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}\n{traceback.format_exc()}")
    finally:
        cur.close()
        conn.close()


async def admin_check_mpesa_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute("""
            SELECT id, user_id, amount, final_amount, address, created_at, 
                   mpesa_conversation_id, mpesa_transaction_id
            FROM withdrawals 
            WHERE status = 'pending_mpesa' 
            ORDER BY created_at DESC
        """)
        pending = cur.fetchall()
        
        if not pending:
            await update.message.reply_text("✅ No pending M-Pesa withdrawals.")
            return
        
        msg = f"🔄 *Pending M-Pesa Withdrawals* ({len(pending)})\n\n"
        for w in pending:
            created = w['created_at'].strftime('%Y-%m-%d %H:%M')
            age_minutes = int((datetime.now() - w['created_at']).total_seconds() / 60)
            msg += f"┃ ID: #{w['id']}\n"
            msg += f"┃ User: `{w['user_id']}`\n"
            msg += f"┃ Amount: ${float(w['amount']):.2f}\n"
            msg += f"┃ Phone: {w['address']}\n"
            msg += f"┃ ConvID: `{w['mpesa_conversation_id']}`\n"
            msg += f"┃ Created: {created}\n"
            msg += f"┃ Age: {age_minutes} minutes\n"
            msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
            
            if len(msg) > 3500:
                await update.message.reply_text(msg, parse_mode="Markdown")
                msg = ""
        
        if msg:
            await update.message.reply_text(msg, parse_mode="Markdown")
        
        await update.message.reply_text(
            "💡 *How to fix:*\n"
            "• `/fixmpesa <id> refund` - Refund the user\n"
            "• `/fixmpesa <id> confirm <txn_id>` - Confirm the withdrawal",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    finally:
        cur.close()
        conn.close()


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("Unauthorized")
        return
    global _broadcast_running, _last_broadcast_time
    now = time.time()
    if now - _last_broadcast_time < BROADCAST_COOLDOWN and not is_super_admin(admin_id):
        remaining = int(BROADCAST_COOLDOWN - (now - _last_broadcast_time))
        await update.message.reply_text(f"Wait {remaining}s")
        return
    broadcast_text = None
    broadcast_photo = None
    broadcast_caption = None
    if update.message.reply_to_message:
        replied = update.message.reply_to_message
        if replied.photo:
            broadcast_photo = replied.photo[-1].file_id
            broadcast_caption = replied.caption or ""
        elif replied.text:
            broadcast_text = replied.text
        else:
            await update.message.reply_text("Reply has no text/photo")
            return
    else:
        if not context.args:
            await update.message.reply_text("Usage: /broadcast <message> or reply to message with /broadcast")
            return
        broadcast_text = " ".join(context.args)
    async with _broadcast_lock:
        if _broadcast_running:
            await update.message.reply_text("Broadcast already running")
            return
        _broadcast_running = True
        _last_broadcast_time = now
    try:
        await update.message.reply_text("Starting broadcast...")
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE data->>'banned' != 'true' OR data->>'banned' IS NULL;")
        users = cur.fetchall()
        cur.close()
        conn.close()
        total = len(users)
        sent = 0
        failed = 0
        status_msg = await update.message.reply_text(f"Broadcasting to {total} users... 0%")
        for i, (u,) in enumerate(users):
            try:
                if broadcast_photo:
                    await context.bot.send_photo(chat_id=int(u), photo=broadcast_photo, caption=broadcast_caption)
                else:
                    await context.bot.send_message(chat_id=int(u), text=broadcast_text)
                sent += 1
            except:
                failed += 1
            if (i+1) % 20 == 0 or (i+1) == total:
                try:
                    pct = int((i+1)/total*100)
                    await status_msg.edit_text(f"Broadcasting {pct}% ({i+1}/{total}) Sent: {sent} Failed: {failed}")
                except:
                    pass
            await asyncio.sleep(0.05)
        await status_msg.edit_text(f"Broadcast done! Total: {total} Sent: {sent} Failed: {failed}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
    finally:
        _broadcast_running = False

async def earn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    global _broadcast_running, _last_broadcast_time
    
    now = time.time()
    if now - _last_broadcast_time < BROADCAST_COOLDOWN:
        remaining = int(BROADCAST_COOLDOWN - (now - _last_broadcast_time))
        await update.message.reply_text(f"⏳ Please wait {remaining} seconds before broadcasting again.")
        return
    
    async with _broadcast_lock:
        if _broadcast_running:
            await update.message.reply_text("⚠️ A broadcast is already in progress. Please wait.")
            return
        
        _broadcast_running = True
        _last_broadcast_time = now
    
    try:
        await update.message.reply_text("📢 Sending earn reminder to users with zero balance...")
        
        task_pay = get_task_pay()
        referral_bonus = get_referral_bonus()
        bot_name = get_bot_name()
        
        message = f"💰 *Earn Money with {bot_name}!*\n\n"
        message += f"Complete simple Gmail registration tasks and earn *${task_pay:.2f}* per task!\n\n"
        message += f"🎁 Refer friends and earn *${referral_bonus:.2f}* per referral!\n\n"
        message += "💸 Withdraw your earnings anytime!\n\n"
        message += "👉 Click *🚀 Start Earning* to get started!"
        
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE (data->>'available_balance')::numeric = 0 AND (data->>'banned' != 'true' OR data->>'banned' IS NULL);")
        users = cur.fetchall()
        cur.close()
        conn.close()
        
        total_users = len(users)
        sent_count = 0
        failed_count = 0
        
        status_message = await update.message.reply_text(f"📢 Sending to {total_users} users... 0%")
        
        for i, (user_id,) in enumerate(users):
            try:
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=message,
                    parse_mode="Markdown"
                )
                sent_count += 1
            except Exception as e:
                failed_count += 1
            
            if (i + 1) % 10 == 0:
                try:
                    percentage = int((i + 1) / total_users * 100)
                    await status_message.edit_text(
                        f"📢 Sending... {percentage}%\n"
                        f"✅ Sent: {sent_count}\n"
                        f"❌ Failed: {failed_count}"
                    )
                except:
                    pass
            
            await asyncio.sleep(0.1)
        
        await status_message.edit_text(
            f"✅ Earn reminder sent!\n\n"
            f"📊 Total Users: {total_users}\n"
            f"✅ Sent: {sent_count}\n"
            f"❌ Failed: {failed_count}"
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    
    finally:
        _broadcast_running = False

async def update_rates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ You are not authorized to run this command.")
        return
    
    url = "https://open.er-api.com/v6/latest/USD"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            data = response.json()
        if data.get("result") != "success":
            await update.message.reply_text("⚠️ Failed to fetch rates.")
            return
        rates = data["rates"]
        conn = get_conn()
        cur = conn.cursor()
        updated_count = 0
        for code, rate in rates.items():
            cur.execute("SELECT code FROM currencies WHERE code = %s", (code,))
            if cur.fetchone():
                cur.execute("UPDATE currencies SET rate_to_usd = %s, updated_at = CURRENT_TIMESTAMP WHERE code = %s", (rate, code))
                updated_count += 1
        # FIXED: Force USD to KES to 112 regardless of API
        update_usd_to_kes_rate(112.0)
        print(f"✅ Updated USD to KES rate: 112.00 (fixed)")
        if "KES" in rates:
            print(f"(API rate {rates['KES']:.2f} ignored - using fixed 112)")
        conn.commit()
        cur.close()
        conn.close()
        await update.message.reply_text(f"✅ Currency rates updated successfully! Updated {updated_count} currencies.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error fetching rates: {str(e)}")

async def debug_currencies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = str(update.effective_user.id)
    if not is_admin(admin_id):
        await update.message.reply_text("❌ Unauthorized")
        return
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM currencies")
        count = cur.fetchone()[0]
        cur.execute("SELECT code, name FROM currencies ORDER BY code LIMIT 10")
        sample = cur.fetchall()
        msg = f"📊 *Currencies Debug*\n\nTotal currencies in DB: {count}\n\nSample (first 10):\n"
        for code, name in sample:
            msg += f"• `{code}` - {name}\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        cur.close()
        conn.close()

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute("SELECT data FROM users WHERE user_id = %s;", (uid,))
        row = cur.fetchone()
        
        if row:
            user = row["data"]
            
            user["awaiting_new_task"] = False
            user["task_creator_admin"] = None
            user["awaiting_withdraw_amount"] = False
            user["awaiting_mpesa"] = False
            user["pending_withdraw_amount"] = 0.0
            user["in_withdraw_menu"] = False
            user["in_balance_menu"] = False
            user["in_refer_menu"] = False
            user["in_profile_menu"] = False
            user["in_settings_menu"] = False
            user["in_support_menu"] = False
            user["in_myaccount_menu"] = False
            user["in_activities_menu"] = False
            user["awaiting_payment_method"] = False
            user["awaiting_payment_address"] = False
            user["selected_payment_method"] = None
            user["choosing_timezone_continent"] = False
            user["choosing_timezone"] = False
            user["selected_continent"] = None
            user["selecting_currency"] = False
            user["currencies_list"] = None
            user["tasks_page"] = 0
            user["history_page"] = 0
            user["activities_page"] = 0
            user["awaiting_withdrawal_selection"] = False
            
            cur.execute("UPDATE users SET data = %s WHERE user_id = %s;", (Json(user), uid))
            conn.commit()
            
            await update.message.reply_text(
                "✅ *Operation Cancelled*\n\n"
                "All operations have been cancelled. You're back to the main menu.",
                parse_mode="Markdown",
                reply_markup=main_menu()
            )
        else:
            await update.message.reply_text("❌ User not found.", reply_markup=main_menu())
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    finally:
        cur.close()
        conn.close()


if DATABASE_URL:
    init_database()
    # Migration for missing columns discovered from logs
    try:
        conn = get_conn()
        cur = conn.cursor()
        # mpesa_b2c_requests status column missing
        cur.execute("ALTER TABLE mpesa_b2c_requests ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending'")
        cur.execute("ALTER TABLE mpesa_b2c_requests ADD COLUMN IF NOT EXISTS result_code TEXT")
        cur.execute("ALTER TABLE mpesa_b2c_requests ADD COLUMN IF NOT EXISTS result_desc TEXT")
        cur.execute("ALTER TABLE mpesa_b2c_requests ADD COLUMN IF NOT EXISTS transaction_id TEXT")
        cur.execute("ALTER TABLE mpesa_b2c_requests ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        cur.execute("ALTER TABLE mpesa_b2c_requests ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        # mpesa_callbacks columns (ensure)
        cur.execute("ALTER TABLE mpesa_callbacks ADD COLUMN IF NOT EXISTS conversation_id TEXT")
        cur.execute("ALTER TABLE mpesa_callbacks ADD COLUMN IF NOT EXISTS originator_conversation_id TEXT")
        cur.execute("ALTER TABLE mpesa_callbacks ADD COLUMN IF NOT EXISTS result_code INTEGER")
        cur.execute("ALTER TABLE mpesa_callbacks ADD COLUMN IF NOT EXISTS result_desc TEXT")
        cur.execute("ALTER TABLE mpesa_callbacks ADD COLUMN IF NOT EXISTS raw_data JSONB")
        cur.execute("ALTER TABLE mpesa_callbacks ADD COLUMN IF NOT EXISTS callback_data JSONB")
        # withdrawals columns needed for mpesa
        cur.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS mpesa_conversation_id TEXT")
        cur.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS mpesa_transaction_id TEXT")
        cur.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS mpesa_result TEXT")
        cur.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS mpesa_result_code TEXT")
        cur.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS mpesa_receipt TEXT")
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ DB migrations applied")
    except Exception as e:
        logger.error(f"DB migration failed: {e} - {traceback.format_exc()}")
else:
    logger.warning("DATABASE_URL not set - skipping DB init")

async def telegram_error_handler(update, context):
    """Global PTB error handler; log failures without breaking the bot."""
    try:
        logger.error("Unhandled Telegram update error: %s", context.error, exc_info=context.error)
    except Exception:
        logger.exception("Failed to log Telegram update error")



async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle @bot M-Pesa - shows last 3 processed M-Pesa withdrawals as viral proof"""
    try:
        raw_query = (update.inline_query.query or '').strip()
        query_lower = raw_query.lower()
        from_user = update.inline_query.from_user
        from_user_id = str(from_user.id) if from_user else "8259158297"
        bot_username = get_bot_name() or "gmailfarmingkebot"
        # clean bot username (remove @)
        bot_username = bot_username.replace("@","").strip() or "gmailfarmingkebot"

        # Only respond to mpesa related queries - if you want it to work for empty query too, allow empty
        normalized = query_lower.replace("-", "").replace(" ", "")
        is_mpesa_query = (not raw_query) or ("mpesa" in normalized) or ("pesa" in normalized)

        if not is_mpesa_query:
            await update.inline_query.answer([], cache_time=10)
            return

        # Fetch last 3 processed mpesa withdrawals FOR THIS USER ONLY
        withdrawals = []
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT amount, final_amount, address, mpesa_receipt, mpesa_transaction_id, 
                       mpesa_conversation_id, created_at, user_id
                FROM withdrawals 
                WHERE status = 'processed' 
                  AND payment_method = 'mpesa'
                  AND user_id = %s
                ORDER BY created_at DESC 
                LIMIT 3
            """, (from_user_id,))
            withdrawals = cur.fetchall()
            cur.close()
            conn.close()
        except Exception as db_e:
            logger.error(f"Inline query DB error: {db_e}")
            withdrawals = []

        # If user has no withdrawals, return empty (or you can show a prompt)
        if not withdrawals:
            # Optional: show a "no transactions yet" article to encourage earning
            no_tx_text = """\U0001f4b0 Start Earning with Gmail Farming! \U0001f389

You haven't withdrawn yet - but you can start now!

\U0001f4b8 Earn $0.10 - $0.25 per Gmail account
\U0001f4f1 Withdraw instantly via M-Pesa

Tap below to start! \U0001f447"""
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001f4b0 Start earning now \u2705", url="https://t.me/gmailfarmingkebot?start=8259158297")]
            ])
            results = [
                InlineQueryResultArticle(
                    id=f"no_tx_{from_user_id}",
                    title="\U0001f4b0 No withdrawals yet - Start earning!",
                    description="Tap to start earning and withdraw via M-Pesa",
                    input_message_content=InputTextMessageContent(message_text=no_tx_text),
                    reply_markup=keyboard
                )
            ]
            await update.inline_query.answer(results, cache_time=1, is_personal=True, auto_pagination=False)
            return


        results = []
        for idx, w in enumerate(withdrawals):
            amount = float(w.get('amount') or w.get('final_amount') or 0)
            phone = w.get('address') or "254745024970"
            # Keep KES part as 0.00 per your format, but we have only USD amount
            tx_id = w.get('mpesa_receipt') or w.get('mpesa_transaction_id') or w.get('mpesa_conversation_id') or f"UHU{random.randint(100,999)}RBBF"
            tx_id = str(tx_id).strip().upper()[:12]
            created = w.get('created_at')
            if isinstance(created, datetime):
                try:
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Africa/Nairobi"))
                    date_full = created.strftime("%Y-%m-%d %H:%M:%S EAT (GMT+3)")
                    date_short = created.strftime("%Y-%m-%d %H:%M:%S EAT")
                except:
                    date_full = created.strftime("%Y-%m-%d %H:%M:%S")
                    date_short = date_full
            else:
                now = datetime.now(ZoneInfo("Africa/Nairobi"))
                date_full = now.strftime("%Y-%m-%d %H:%M:%S EAT (GMT+3)")
                date_short = now.strftime("%Y-%m-%d %H:%M:%S EAT")

            # --- What user SEES in inline suggestion list ---
            title = f"💰 Withdrawal Successful! ${amount:.2f}"
            description = f"{phone} | {tx_id} | {date_short}"

            # --- Detailed format for preview (optional, we keep title/desc above) ---
            detailed_preview = f"""\U0001f4b0 Withdrawal Successful! \U0001f389

Your M-Pesa withdrawal has been processed successfully!

━━━━━━━━━━━━━━━━━━━━
\U0001f4cb Transaction Details:
━━━━━━━━━━━━━━━━━━━━

\U0001f4b3 Amount: ${amount:.2f}
\U0001f4f1 Received: 0.00 KES
\U0001f4f1 Phone: {phone}
\U0001f194 Transaction ID: {tx_id}
\U0001f4c5 Date: {date_full}

━━━━━━━━━━━━━━━━━━━━

\U0001f4cc Funds have been sent to your M-Pesa account.
\U0001f4a1 Check your M-Pesa messages for confirmation.

If you haven't received the money within 10 minutes,
please contact support. @gmsupportdeskbot"""

            # --- What actually gets SENT when they click (viral message) ---
            viral_message = f"""\U0001f4b0 I just Withdrew real money from Gmail Farming \U0001f389

\U0001f4b3 Amount: ${amount:.2f}
\U0001f4f1 Phone: {phone}
\U0001f194 Transaction ID: {tx_id}
\U0001f4c5 Date: {date_short}

Next it could be you mate \U0001f609"""

            referral_link = "https://t.me/gmailfarmingkebot?start=8259158297"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001f4b0 Start earning now \u2705", url=referral_link)]
            ])

            # We send viral_message + button, but title/description shows it's a real withdrawal
            # For better social proof, we use detailed_preview in article's description via title
            results.append(
                InlineQueryResultArticle(
                    id=f"mpesa_{idx}_{tx_id}_{int(time.time())}",
                    title=title,
                    description=description,
                    input_message_content=InputTextMessageContent(
                        message_text=viral_message
                    ),
                    reply_markup=keyboard
                )
            )

        await update.inline_query.answer(results, cache_time=1, is_personal=True, auto_pagination=False)

    except Exception as e:
        logger.error(f"Inline query error: {e} - {traceback.format_exc()}")
        try:
            await update.inline_query.answer([], cache_time=1)
        except:
            pass

if BOT_TOKEN:
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        application.add_error_handler(telegram_error_handler)
        logger.info("✅ Application built")
    except Exception as e:
        logger.error(f"Failed to build application: {e}")
        application = None
else:
    logger.error("BOT_TOKEN missing! Set BOT_TOKEN env var")
    application = None

# === FIXED: All handlers inside if application ===
if application:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("dashboard", admin_dashboard))
    application.add_handler(CommandHandler("addtask", add_task))
    application.add_handler(CommandHandler("add_task", add_task))
    application.add_handler(CommandHandler("addtask_interactive", add_task_interactive))
    application.add_handler(CommandHandler("deletetask", delete_task))
    application.add_handler(CommandHandler("viewtasks", view_tasks))
    application.add_handler(CommandHandler("admins", list_admins))
    application.add_handler(CommandHandler("addadmin", add_admin))
    application.add_handler(CommandHandler("setadminapprovalgroup", set_other_admin_approval_group))
    application.add_handler(CommandHandler("removeadmin", remove_admin))
    application.add_handler(CommandHandler("setmyapprovalgroup", set_my_approval_group))
    application.add_handler(CommandHandler("fixstuck", admin_fix_stuck))
    application.add_handler(CommandHandler("fixmpesa", admin_fix_mpesa_withdrawal))
    application.add_handler(CommandHandler("checkmpesa", admin_check_mpesa_withdrawals))
    application.add_handler(CommandHandler("message", admin_message))
    application.add_handler(CommandHandler("userinfo", admin_userinfo))
    application.add_handler(CommandHandler("warning", admin_warning))
    application.add_handler(CommandHandler("ban", admin_ban))
    application.add_handler(CommandHandler("unban", admin_unban))
    application.add_handler(CommandHandler("banned", admin_banned_users))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("users", admin_users))
    application.add_handler(CommandHandler("withdrawals", admin_withdrawals))
    application.add_handler(CommandHandler("balances", admin_balances))
    application.add_handler(CommandHandler("addbalance", admin_addbalance))
    application.add_handler(CommandHandler("reducebalance", admin_reducebalance))
    application.add_handler(CommandHandler("reduceonhold", admin_reduce_onhold_balance))
    application.add_handler(CommandHandler("update_rates", update_rates))
    application.add_handler(CommandHandler("debug_currencies", debug_currencies))
    application.add_handler(CommandHandler("earn", earn_command))
    application.add_handler(CommandHandler("setwithdrawal", admin_set_withdrawal))
    application.add_handler(CommandHandler("settaskpay", admin_set_taskpay))
    application.add_handler(CommandHandler("setbotusername", admin_set_bot_username))
    application.add_handler(CommandHandler("setsupportbot", admin_set_support_bot))
    application.add_handler(CommandHandler("setadminid", admin_set_admin_id))
    application.add_handler(CommandHandler("setannounce", admin_set_announce_channel))
    application.add_handler(CommandHandler("setrefbonus", admin_set_referral_bonus))
    application.add_handler(CommandHandler("setbotname", admin_set_bot_name))
    application.add_handler(CommandHandler("setdailybonus", admin_set_daily_bonus))
    application.add_handler(CommandHandler("setusdkesrate", admin_set_usd_kes_rate))
    application.add_handler(CommandHandler("botsettings", admin_show_all_settings))
    application.add_handler(CommandHandler("settings", admin_settings_view))
    application.add_handler(CommandHandler("setup", admin_setup_wizard))
    application.add_handler(CommandHandler("cancelsetup", admin_cancel_setup))
    application.add_handler(CommandHandler("setapprovalgroup", admin_set_approval_group_cmd))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(InlineQueryHandler(inline_query))
    application.add_handler(CallbackQueryHandler(admin_callbacks, pattern="^admin_"))
    application.add_handler(CallbackQueryHandler(callbacks, pattern="^(?!admin_).*"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

# === FIXED: Background loop that keeps running ===
def start_bot_loop():
    global loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def init_bot():
        if not application:
            logger.error("Application not built, skipping bot start")
            return
        await application.initialize()
        await application.start()
        if application.job_queue:
            await application.job_queue.start()
            logger.info("✅ Job queue started")
        logger.info("✅ Telegram bot started and running")

    try:
        loop.run_until_complete(init_bot())
        loop.run_forever()
    except Exception as e:
        logger.error(f"Bot loop crashed: {e} - {traceback.format_exc()}")
    finally:
        logger.warning("Bot loop stopped")

if application:
    import threading
    bot_thread = threading.Thread(target=start_bot_loop, daemon=True)
    bot_thread.start()
    time.sleep(1.5)
    logger.info(f"Loop running: {loop.is_running() if loop else False}")
else:
    loop = None
    logger.error("Application not initialized - check BOT_TOKEN")

@app.route("/")
def home():
    return jsonify({
        "status": "ok", 
        "bot": get_bot_name(),
        "application_initialized": application is not None,
        "loop_initialized": loop is not None,
        "loop_running": loop.is_running() if loop and hasattr(loop, 'is_running') else False,
        "bot_token_set": bool(BOT_TOKEN),
        "db_set": bool(DATABASE_URL)
    })

@app.route("/health")
def health():
    return jsonify({"status": "healthy", "token": bool(BOT_TOKEN), "db": bool(DATABASE_URL)})

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if not application or not loop:
            logger.error("Webhook called but application not initialized")
            return "Bot not initialized - check BOT_TOKEN env", 500
        if not loop.is_running():
            logger.error("Webhook called but loop not running!")
            return "Loop not running", 500
        data = request.get_json(force=True)
        if not data:
            return "no data", 400
        update = Update.de_json(data, application.bot)
        future = asyncio.run_coroutine_threadsafe(application.process_update(update), loop)
        try:
            future.result(timeout=1.0)
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.error(f"process_update failed: {e} - {traceback.format_exc()}")
        return "ok", 200
    except Exception as e:
        logger.error(f"WEBHOOK ERROR: {e} - {traceback.format_exc()}")
        return "ok", 200

@app.route("/set_webhook", methods=["GET", "POST"])
def set_webhook_route():
    if not BOT_TOKEN:
        return jsonify({"error": "BOT_TOKEN not set"}), 500

    base_url = request.url_root.rstrip("/").replace("http://", "https://")
    body = request.get_json(silent=True) if request.is_json else {}
    body = body or {}
    custom_url = request.args.get("url") or body.get("url") or base_url
    webhook_url = f"{custom_url.rstrip('/')}/webhook"

    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                json={"url": webhook_url}
            )
        result = r.json()
        logger.info(f"setWebhook result: {result}")
        status = 200 if r.is_success and result.get("ok") else 502
        return jsonify({"webhook_url": webhook_url, "telegram_response": result}), status
    except Exception as e:
        logger.error(f"setWebhook error: {e}")
        return jsonify({"error": str(e), "webhook_url": webhook_url}), 500


@app.route("/fix_pending_withdrawals", methods=["GET"])
def fix_pending_withdrawals():
    """One-time cleanup: refunds all pending_mpesa withdrawals whose B2C callback was 'insufficient balance' or failed"""
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # Find pending withdrawals
        cur.execute("SELECT * FROM withdrawals WHERE status = 'pending_mpesa'")
        pending = cur.fetchall()
        fixed = 0
        for w in pending:
            conv_id = w.get('mpesa_conversation_id')
            # Check if we have a failed callback for this conv
            cur.execute("SELECT * FROM mpesa_callbacks WHERE conversation_id = %s OR raw_data::text LIKE %s ORDER BY received_at DESC LIMIT 1", (conv_id, f'%{conv_id}%'))
            cb = cur.fetchone()
            if cb:
                # If result code is not 0, refund
                raw = cb.get('raw_data') or cb.get('callback_data') or {}
                # try to parse result code
                try:
                    # raw is dict
                    result = raw.get('Result', {}) if isinstance(raw, dict) else {}
                    code = result.get('ResultCode', 1)
                except:
                    code = 1
                if str(code) != '0':
                    # refund logic
                    withdrawal_id = w['id']
                    user_id = w['user_id']
                    amount = float(w['amount'])
                    # get user data
                    cur.execute("SELECT data FROM users WHERE user_id = %s", (user_id,))
                    row = cur.fetchone()
                    if row:
                        user_data = row['data']
                        # refund available_balance
                        user_data['available_balance'] = float(user_data.get('available_balance', 0)) + amount
                        cur.execute("UPDATE users SET data = %s WHERE user_id = %s", (Json(user_data), user_id))
                        cur.execute("UPDATE withdrawals SET status = 'failed', mpesa_result = %s, processed_at = CURRENT_TIMESTAMP WHERE id = %s", (f"Refunded by fix_pending - insufficient balance, code {code}", withdrawal_id))
                        fixed += 1
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"fixed_count": fixed, "message": f"Refunded {fixed} pending withdrawals. Users balances restored. Now top up M-Pesa float."})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)
