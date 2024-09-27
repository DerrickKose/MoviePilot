from typing import Optional, Tuple, Union, Any, List, Generator

from app import schemas
from app.core.context import MediaInfo
from app.log import logger
from app.modules import _ModuleBase, _MediaServerBase
from app.modules.plex.plex import Plex
from app.schemas import MediaServerConf
from app.schemas.types import MediaType


class PlexModule(_ModuleBase, _MediaServerBase[Plex]):

    def init_module(self) -> None:
        """
        初始化模块
        """
        super().init_service(service_name=Plex.__name__.lower(),
                             service_type=lambda conf: Plex(**conf.config, sync_libraries=conf.sync_libraries))

    @staticmethod
    def get_name() -> str:
        return "Plex"

    def stop(self):
        pass

    def test(self) -> Optional[Tuple[bool, str]]:
        """
        测试模块连接性
        """
        if not self._instances:
            return None
        for name, server in self._instances.items():
            if server.is_inactive():
                server.reconnect()
            if not server.get_librarys():
                return False, f"无法连接Plex服务器：{name}"
        return True, ""

    def init_setting(self) -> Tuple[str, Union[str, bool]]:
        pass

    def scheduler_job(self) -> None:
        """
        定时任务，每10分钟调用一次
        """
        # 定时重连
        for name, server in self._instances.items():
            if server.is_inactive():
                logger.info(f"Plex {name} 服务器连接断开，尝试重连 ...")
                server.reconnect()

    def webhook_parser(self, body: Any, form: Any, args: Any) -> Optional[schemas.WebhookEventInfo]:
        """
        解析Webhook报文体
        :param body:  请求体
        :param form:  请求表单
        :param args:  请求参数
        :return: 字典，解析为消息时需要包含：title、text、image
        """
        source = args.get("source")
        if source:
            server_config: MediaServerConf = self.get_config(source, 'plex')
            if not server_config:
                return None
            server: Plex = self.get_instance(source)
            if not server:
                return None
            return server.get_webhook_message(body)

        for conf in self._configs.values():
            if conf.type != "plex":
                continue
            server = self.get_instance(conf.name)
            if server:
                result = server.get_webhook_message(body)
                if result:
                    return result
        return None

    def media_exists(self, mediainfo: MediaInfo, itemid: str = None) -> Optional[schemas.ExistMediaInfo]:
        """
        判断媒体文件是否存在
        :param mediainfo:  识别的媒体信息
        :param itemid:  媒体服务器ItemID
        :return: 如不存在返回None，存在时返回信息，包括每季已存在所有集{type: movie/tv, seasons: {season: [episodes]}}
        """
        for name, server in self._instances.items():
            if mediainfo.type == MediaType.MOVIE:
                if itemid:
                    movie = server.get_iteminfo(itemid)
                    if movie:
                        logger.info(f"媒体库 {name} 中找到了 {movie}")
                        return schemas.ExistMediaInfo(
                            type=MediaType.MOVIE,
                            server=name,
                            itemid=movie.item_id
                        )
                movies = server.get_movies(title=mediainfo.title,
                                           original_title=mediainfo.original_title,
                                           year=mediainfo.year,
                                           tmdb_id=mediainfo.tmdb_id)
                if not movies:
                    logger.info(f"{mediainfo.title_year} 没有在媒体库 {name} 中")
                    continue
                else:
                    logger.info(f"媒体库 {name} 中找到了 {movies}")
                    return schemas.ExistMediaInfo(
                        type=MediaType.MOVIE,
                        server=name,
                        itemid=movies[0].item_id
                    )
            else:
                item_id, tvs = server.get_tv_episodes(title=mediainfo.title,
                                                      original_title=mediainfo.original_title,
                                                      year=mediainfo.year,
                                                      tmdb_id=mediainfo.tmdb_id,
                                                      item_id=itemid)
                if not tvs:
                    logger.info(f"{mediainfo.title_year} 没有在媒体库 {name} 中")
                    continue
                else:
                    logger.info(f"{mediainfo.title_year} 在媒体库 {name} 中找到了这些季集：{tvs}")
                    return schemas.ExistMediaInfo(
                        type=MediaType.TV,
                        seasons=tvs,
                        server=name,
                        itemid=item_id
                    )
        return None

    def media_statistic(self, server: str = None) -> Optional[List[schemas.Statistic]]:
        """
        媒体数量统计
        """
        if server:
            server: Plex = self.get_instance(server)
            if not server:
                return None
            servers = [server]
        else:
            servers = self._instances.values()
        media_statistics = []
        for server in servers:
            media_statistic = server.get_medias_count()
            if not media_statistics:
                continue
            media_statistics.append(media_statistic)
        return media_statistics

    def mediaserver_librarys(self, server: str = None, hidden: bool = False,
                             **kwargs) -> Optional[List[schemas.MediaServerLibrary]]:
        """
        媒体库列表
        """
        server: Plex = self.get_instance(server)
        if server:
            return server.get_librarys(hidden)
        return None

    def mediaserver_items(self, server: str, library_id: str, start_index: int = 0, limit: int = 100) \
            -> Optional[Generator]:
        """
        媒体库项目列表
        """
        server: Plex = self.get_instance(server)
        if server:
            return server.get_items(library_id, start_index, limit)
        return None

    def mediaserver_iteminfo(self, server: str, item_id: str) -> Optional[schemas.MediaServerItem]:
        """
        媒体库项目详情
        """
        server: Plex = self.get_instance(server)
        if server:
            return server.get_iteminfo(item_id)
        return None

    def mediaserver_tv_episodes(self, server: str,
                                item_id: Union[str, int]) -> Optional[List[schemas.MediaServerSeasonInfo]]:
        """
        获取剧集信息
        """
        server: Plex = self.get_instance(server)
        if not server:
            return None
        _, seasoninfo = server.get_tv_episodes(item_id=item_id)
        if not seasoninfo:
            return []
        return [schemas.MediaServerSeasonInfo(
            season=season,
            episodes=episodes
        ) for season, episodes in seasoninfo.items()]

    def mediaserver_playing(self, server: str, count: int = 20, **kwargs) -> List[schemas.MediaServerPlayItem]:
        """
        获取媒体服务器正在播放信息
        """
        server: Plex = self.get_instance(server)
        if not server:
            return []
        return server.get_resume(num=count)

    def mediaserver_latest(self, server: str, count: int = 20, **kwargs) -> List[schemas.MediaServerPlayItem]:
        """
        获取媒体服务器最新入库条目
        """
        server: Plex = self.get_instance(server)
        if not server:
            return []
        return server.get_latest(num=count)

    def mediaserver_play_url(self, server: str, item_id: Union[str, int]) -> Optional[str]:
        """
        获取媒体库播放地址
        """
        server: Plex = self.get_instance(server)
        if not server:
            return None
        return server.get_play_url(item_id)
