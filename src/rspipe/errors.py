"""rspipe 异常层次。"""

from __future__ import annotations


class RspipeError(Exception):
    """所有 rspipe 异常的基类。"""


class ConfigError(RspipeError):
    """配置文件缺失、非法或互相矛盾。"""


class CredentialError(RspipeError):
    """缺少必需的访问凭证。"""


class ProviderError(RspipeError):
    """数据源检索 / 下载失败。"""


class SearchError(ProviderError):
    """检索失败。"""


class DownloadError(ProviderError):
    """下载失败（含完整性校验失败）。"""


class UnpackError(RspipeError):
    """归档解包或产品结构解析失败。"""


class MetadataError(RspipeError):
    """元数据（MTL / MTD / STAC 属性）缺失或非法。"""


class NormalizeError(RspipeError):
    """辐射归一化 / 重投影失败。"""


class CogError(RspipeError):
    """COG 写出或校验失败。"""


class CloudMaskError(RspipeError):
    """云掩膜生成失败。"""


class ColorBalanceError(RspipeError):
    """匀色失败。"""


class MosaicError(RspipeError):
    """镶嵌失败。"""


class StacError(RspipeError):
    """STAC 对象构建 / 发布 / 索引失败。"""


class PipelineError(RspipeError):
    """流水线编排失败。"""


class StepSkippedError(RspipeError):
    """步骤被跳过（幂等：产物已存在）。"""
