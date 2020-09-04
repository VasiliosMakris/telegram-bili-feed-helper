import logging
import uvloop

# logging.basicConfig(
#     format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
# )

logger = logging.getLogger("Telegram_Bili_Feed_Helper")
logger.setLevel(logging.DEBUG)

formater = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(funcName)s[%(module)s:%(lineno)d] - %(message)s"
)
streamhandler = logging.StreamHandler()
streamhandler.setLevel(logging.INFO)
streamhandler.setFormatter(formater)
logger.addHandler(streamhandler)

filehandler = logging.handlers.RotatingFileHandler(
    "bili_feed.log", maxBytes=1048576, backupCount=5, encoding="utf-8"
)
filehandler.setLevel(logging.DEBUG)
filehandler.setFormatter(formater)
logger.addHandler(filehandler)

headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36"
}

uvloop.install()
