"""QQ发送正文投影；原生模型响应与历史记录不在这里改写。"""
import re


def without_comments(text: str) -> str:
    # 未闭合的注释也不能把后续草稿发给联系人；不处理其他HTML或角色正文。
    return re.sub(r'<!--[\s\S]*?(?:-->|$)', '', text).strip()
