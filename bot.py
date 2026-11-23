import os, re, json, datetime, difflib
import aiohttp, discord
from discord.ext import tasks
from bs4 import BeautifulSoup
from google import genai

TOKEN = os.environ["DISCORD_TOKEN"]
RULES_CHANNEL_ID = int(os.environ["RULES_CHANNEL_ID"])
ASK_CHANNEL_ID = int(os.environ["ASK_CHANNEL_ID"])
MODEL = "gemini-2.5-flash"  # free-tier Gemini model

# Firm name -> list of pages that contain its account rules
FIRMS = {
    # Prop Firm Match pages first (same layout for every firm), official site second.
    # If a page shows "couldn't read" in the logs, fix that link here.
    # CFD
    "The5ers": ["https://propfirmmatch.com/prop-firms/the5ers?tab=rules", "https://the5ers.com"],
    "FundingPips": ["https://propfirmmatch.com/prop-firms/funding-pips?tab=rules", "https://fundingpips.com"],
    "FundedNext": ["https://propfirmmatch.com/prop-firms/fundednext?tab=rules", "https://fundednext.com"],
    "Blueberry Funded": ["https://propfirmmatch.com/prop-firms/blueberry-funded?tab=rules", "https://blueberryfunded.com"],
    "FTMO": ["https://propfirmmatch.com/prop-firms/ftmo?tab=rules", "https://ftmo.com/en/"],
    "Sure Leverage Funding": ["https://propfirmmatch.com/prop-firms/sure-leverage-funding?tab=rules", "https://sureleveragefunding.com"],
    "Tradeify FX": ["https://propfirmmatch.com/prop-firms/tradeify-fx?tab=rules"],
    "Blue Guardian": ["https://propfirmmatch.com/prop-firms/blue-guardian?tab=rules", "https://blueguardian.com"],
    # Futures
    "Lucid Trading": ["https://propfirmmatch.com/futures/prop-firms/lucid-trading?tab=rules", "https://lucidtrading.com"],
    "Apex": ["https://propfirmmatch.com/futures/prop-firms/apex-trader-funding?tab=rules", "https://apextraderfunding.com"],
    "Tradeify": ["https://propfirmmatch.com/futures/prop-firms/tradeify?tab=rules", "https://tradeify.co"],
    "My Funded Futures": ["https://propfirmmatch.com/futures/prop-firms/my-funded-futures?tab=rules", "https://myfundedfutures.com"],
    "The5ers Futures": ["https://propfirmmatch.com/futures/prop-firms/5ers-futures?tab=rules"],
    "FundedNext Futures": ["https://propfirmmatch.com/futures/prop-firms/fundednext-futures?tab=rules"],
    "Blue Guardian Futures": ["https://propfirmmatch.com/futures/prop-firms/blueguardian-futures?tab=rules"],
    # add more firms the same way
}

# 8:00 IST = 02:30 UTC
CHECK_TIME = datetime.time(hour=2, minute=30, tzinfo=datetime.timezone.utc)

gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

PROMPT = """From the text below, list every account plan of the prop firm {firm}.
Return ONLY a JSON list. Each item: {{"account": "Select 25K", "rules": "..."}}.
"rules" is a short bullet list (one string, lines starting with '- ') covering, in this order:
price, profit target, max drawdown and its type, daily loss limit, consistency rule,
minimum trading days, payout rules, contract limits, and any other important rule.
Use only what the text says. Keep the same wording style every time."""


def key_of(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


async def fetch_text(session, url):
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30),
                           headers={"User-Agent": "Mozilla/5.0"}) as r:
        html = await r.text()
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "footer"]):
        t.decompose()
    return " ".join(soup.get_text(" ").split())[:40000]


async def extract(firm, text):
    resp = await gemini.aio.models.generate_content(
        model=MODEL, contents=PROMPT.format(firm=firm) + "\n\n" + text)
    raw = resp.text
    return json.loads(raw[raw.find("["): raw.rfind("]") + 1])


async def stored_messages(channel):
    out = {}
    async for m in channel.history(limit=1000):
        if m.author == bot.user and m.content.startswith("**"):
            title = m.content.split("\n", 1)[0].strip("*")
            out[key_of(title)] = m
    return out


def rules_part(text):
    return text.split("\n_Checked")[0]


@tasks.loop(time=CHECK_TIME)
async def daily_check():
    rules_ch = bot.get_channel(RULES_CHANNEL_ID)
    ask_ch = bot.get_channel(ASK_CHANNEL_ID)
    stored = await stored_messages(rules_ch)
    async with aiohttp.ClientSession() as s:
        for firm, urls in FIRMS.items():
            try:
                text = " ".join([await fetch_text(s, u) for u in urls])
                plans = await extract(firm, text)
            except Exception as e:
                print(f"{firm} failed: {e}")
                continue
            for p in plans:
                title = f"{firm} {p['account']}"
                body = f"**{title}**\n{p['rules']}"[:1950]
                body += f"\n_Checked {datetime.date.today()}_"
                old = stored.get(key_of(title))
                if old is None:
                    await rules_ch.send(body)
                elif rules_part(old.content) != rules_part(body):
                    await old.edit(content=body)
                    await ask_ch.send(f"Rules changed for **{title}**:\n{p['rules']}"[:1990])
                else:
                    await old.edit(content=body)


@bot.event
async def on_ready():
    if not daily_check.is_running():
        daily_check.start()
    print("Logged in as", bot.user)


@bot.event
async def on_message(m):
    if m.author.bot or m.channel.id != ASK_CHANNEL_ID:
        return
    if m.content.strip() == "!refresh":
        await m.reply("Checking all firms now...")
        await daily_check()
        await m.reply("Done.")
        return
    stored = await stored_messages(bot.get_channel(RULES_CHANNEL_ID))
    q = key_of(m.content)
    if not q:
        return
    match = difflib.get_close_matches(q, stored.keys(), n=1, cutoff=0.5)
    if match:
        await m.reply(rules_part(stored[match[0]].content))
    else:
        await m.reply("I couldn't find that account. Try something like `Tradeify Select 25K`.")


bot.run(TOKEN)
