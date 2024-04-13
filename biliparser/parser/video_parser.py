import os
import re

import httpx
from telegram.constants import FileSizeLimit
import orjson

from ..cache import (
    CACHES_TIMER,
    RedisCache,
)
from ..model import Video
from ..utils import (
    BILI_API,
    LOCAL_MODE,
    ParserException,
    escape_markdown,
    headers,
    logger,
    retry_catcher,
)
from .reply_parser import parse_reply

QN = [64, 32, 16]


async def __test_url_status_code(client, url, referer):
    header = headers.copy()
    header["Referer"] = referer
    async with client.stream("GET", url, headers=header) as response:
        if response.status_code != 200:
            return False
        return True


async def __get_video_result(client: httpx.AsyncClient, f: Video, detail, qn: int):
    params = {"avid": f.aid, "cid": f.cid}
    if qn:
        params["qn"] = qn
    r = await client.get(
        BILI_API + "/x/player/playurl",
        params=params,
    )
    video_result = r.json()
    logger.debug(f"视频内容: {video_result}")
    if (
        video_result.get("code") == 0
        and video_result.get("data")
        and video_result.get("data").get("durl")
        and video_result.get("data").get("durl")[0].get("size")
        < (
            int(
                os.environ.get(
                    "VIDEO_SIZE_LIMIT", FileSizeLimit.FILESIZE_UPLOAD_LOCAL_MODE
                )
            )
            if LOCAL_MODE
            else FileSizeLimit.FILESIZE_UPLOAD
        )
    ):
        url = video_result["data"]["durl"][0]["url"]
        result = await __test_url_status_code(client, url, f.url)
        if not result and video_result["data"]["durl"][0].get("backup_url", None):
            url = video_result["data"]["durl"][0]["backup_url"]
            result = await __test_url_status_code(client, url, f.url)
        if result:
            f.mediacontent = video_result
            f.mediathumb = detail.get("pic")
            f.mediaduration = round(video_result["data"]["durl"][0]["length"] / 1000)
            f.mediadimention = detail.get("pages")[0].get("dimension")
            f.mediaurls = url
            f.mediatype = "video"
            f.mediaraws = (
                False
                if video_result.get("data").get("durl")[0].get("size")
                < (
                    FileSizeLimit.FILESIZE_DOWNLOAD_LOCAL_MODE
                    if LOCAL_MODE
                    else FileSizeLimit.FILESIZE_DOWNLOAD
                )
                else True
            )
            return True


@retry_catcher
async def parse_video(client: httpx.AsyncClient, url: str):
    logger.info(f"处理视频信息: 链接: {url}")
    match = re.search(
        r"(?:bilibili\.com/(?:video|bangumi/play)|b23\.tv|acg\.tv)/(?:(?P<bvid>BV\w{10})|av(?P<aid>\d+)|ep(?P<epid>\d+)|ss(?P<ssid>\d+)|)/?\??(?:p=(?P<page>\d+))?",
        url,
    )
    match_fes = re.search(
        r"bilibili\.com/festival/(?P<festivalid>\w+)\?(?:bvid=(?P<bvid>BV\w{10}))", url
    )
    if match_fes:
        bvid = match_fes.group("bvid")
        epid = None
        aid = None
        ssid = None
        page = 1
    elif match:
        bvid = match.group("bvid")
        epid = match.group("epid")
        aid = match.group("aid")
        ssid = match.group("ssid")
        page = match.group("page")
        if page and page.isdigit():
            page = max(1, int(page))
        else:
            page = 1
    else:
        raise ParserException("视频链接错误", url)
    if epid:
        params = {"ep_id": epid}
    elif bvid:
        params = {"bvid": bvid}
    elif aid:
        params = {"aid": aid}
    elif ssid:
        params = {"season_id": ssid}
    else:
        raise ParserException("视频链接解析错误", url)
    f = Video(url)
    f.page = page
    if epid:
        f.epid = epid
    if epid is not None or ssid is not None:
        # 1.获取缓存
        try:
            cache = (
                RedisCache().get(f"bangumi:ep:{epid}")
                if epid
                else RedisCache().get(f"bangumi:ss:{ssid}")
            )
        except Exception as e:
            logger.exception(f"拉取番剧缓存错误: {e}")
            cache = None
        # 2.拉取番剧
        if cache:
            logger.info(
                f"拉取番剧缓存:epid {epid}" if epid else f"拉取番剧缓存:ssid {ssid}"
            )
            f.epcontent = orjson.loads(cache)  # type: ignore
        else:
            try:
                r = await client.get(
                    BILI_API + "/pgc/view/web/season",
                    params=params,
                )
                f.epcontent = r.json()
            except Exception as e:
                raise ParserException(f"番剧获取错误:{epid if epid else ssid}", url, e)
            # 3.番剧解析
            if not f.epcontent or not f.epcontent.get("result"):
                # Anime detects non-China IP
                raise ParserException(
                    f"番剧解析错误:{epid if epid else ssid} {f.epcontent}",
                    url,
                    f.epcontent,
                )
            if not f.epid or not f.ssid or not f.aid:
                raise ParserException(
                    f"番剧解析错误:{f.aid} {f.ssid} {f.aid}", url, f.epcontent
                )
            # 4.缓存评论
            try:
                for key in [f"bangumi:ep:{f.epid}", f"bangumi:ss:{f.ssid}"]:
                    RedisCache().set(
                        key,
                        orjson.dumps(f.epcontent),
                        ex=CACHES_TIMER.get("bangumi"),
                        nx=True,
                    )
            except Exception as e:
                logger.exception(f"缓存番剧错误: {e}")
        params = {"aid": f.aid}
        aid = f.aid
    # 1.获取缓存
    try:
        cache = (
            RedisCache().get(f"video:aid:{aid}")
            if aid
            else RedisCache().get(f"video:bvid:{bvid}")
        )
    except Exception as e:
        logger.exception(f"拉取视频缓存错误: {e}")
        cache = None
    # 2.拉取视频
    if cache:
        logger.info(f"拉取视频缓存:{aid if aid else bvid}")
        f.infocontent = orjson.loads(cache)  # type: ignore
    else:
        try:
            r = await client.get(
                BILI_API + "/x/web-interface/view",
                params=params,
            )
            f.infocontent = r.json()
        except Exception as e:
            raise ParserException(f"视频获取错误:{aid if aid else bvid}", url, e)
        # 3.视频解析
        if not f.infocontent and not f.infocontent.get("data"):
            # Video detects non-China IP
            raise ParserException(
                f"视频解析错误{aid if aid else bvid}", r.url, f.infocontent
            )
        if not f.aid or not f.bvid or not f.cid:
            raise ParserException(
                f"视频解析错误:{f.aid} {f.bvid} {f.cid}", url, f.epcontent
            )
        # 4.缓存视频
        try:
            for key in [f"video:aid:{f.aid}", f"video:bvid:{f.bvid}"]:
                RedisCache().set(
                    key,
                    orjson.dumps(f.infocontent),
                    ex=CACHES_TIMER.get("video"),
                    nx=True,
                )
        except Exception as e:
            logger.exception(f"缓存番剧错误: {e}")
    detail = f.infocontent.get("data")
    f.user = detail.get("owner").get("name")
    f.uid = detail.get("owner").get("mid")
    f.content = detail.get("tname", "发布视频")
    if detail.get("pages") and len(detail["pages"]) > 1:
        f.content += f" - 第{page}P/共{len(detail['pages'])}P"
    if detail.get("dynamic") or detail.get("desc"):
        f.content += f" - {detail.get('dynamic') or detail.get('desc')}"
    f.extra_markdown = f"[{escape_markdown(detail.get('title'))}]({f.url})"
    f.mediatitle = detail.get("title")
    f.mediaurls = detail.get("pic")
    f.mediatype = "image"
    f.replycontent = await parse_reply(client, f.aid, f.reply_type)

    for qn in QN:
        if await __get_video_result(client, f, detail, qn):
            break
    return f
