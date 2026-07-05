"""统一异常定义。"""


class QuantPlatformError(Exception):
    """平台根异常。"""


class ConfigError(QuantPlatformError):
    """配置加载错误。"""


class DataSourceError(QuantPlatformError):
    """数据源调用错误（含网络/解析/接口变更）。"""


class DataSourceNotEnabled(DataSourceError):
    """数据源未启用。"""


class DataNotFoundError(QuantPlatformError):
    """请求的数据不存在。"""


class StorageError(QuantPlatformError):
    """存储层错误。"""
