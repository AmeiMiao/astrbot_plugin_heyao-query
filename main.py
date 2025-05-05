import asyncio
import logging
import os
import json
import time
import sys
from pathlib import Path
from typing import Optional, Dict, Any

import requests
from PIL import Image, ImageDraw, ImageFont

# 正确的导入 - 使用AstrBot提供的类型
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api.message_components import Plain, Image as ImageComp # 明确导入 Plain 和 Image 组件

# --- 常量和设置 ---
log = logging.getLogger(__name__)
PLUGIN_DIR = Path(__file__).parent.resolve() # 获取插件文件所在的目录

# --- 辅助函数 ---

def resource_path(relative_path: str) -> Path:
    """获取插件目录内资源的绝对路径。"""
    return PLUGIN_DIR / relative_path

async def fetch_wechat_info(content: str) -> Optional[Dict[str, Any]]:
    """向微信小程序API发送请求。"""
    url = "https://i.qz.fkw.com/appAjax/wxAppConnectionQuery.jsp?cmd=search"
    payload = {
        "wxappAid": "3086825",
        "wxappId": "101",
        "itemId": "103",
        "contentList": json.dumps([{"key": "v2", "value": content}])
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    response = None
    try:
        log.info(f"Attempting to fetch API data for order: {content}")
        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(
            None, # 使用默认执行器
            lambda: requests.post(url, data=payload, headers=headers, timeout=15) # 添加了超时
        )
        response = await future
        log.info(f"Finished API request for order {content}. Status: {response.status_code if response else 'N/A'}")

        if response and response.status_code == 200:
             response_json = response.json()
             log.debug(f"API Response JSON for {content}: {response_json}") # 打印成功时的API响应JSON
             response.raise_for_status() # 对不良响应 (4xx 或 5xx) 抛出 HTTPError
             return response_json
        elif response:
             log.error(f"API returned non-200 status code {response.status_code} for order {content}. Response text: {response.text}") # 记录非200响应
             return None
        else:
             log.error(f"API request failed to get a response for order {content}.") # 记录无响应
             return None

    except requests.exceptions.Timeout:
        log.error(f"API Request Timeout for order {content}")
        return None
    except requests.exceptions.RequestException as e:
        log.error(f"API Request Error for order {content}: {e}")
        return None
    except json.JSONDecodeError as e:
        # 在JSON解析错误时，确保response对象存在并打印其内容，帮助调试
        response_text = response.text if response is not None else "N/A (No response received)"
        log.error(f"Failed to decode API response JSON for order {content}: {e}. Response text: {response_text}")
        return None
    except Exception as e:
         log.error(f"An unexpected error occurred during API request for order {content}: {e}", exc_info=True)
         return None


def generate_image(data: Dict[str, str]) -> Optional[Path]:
    """生成通知图片。"""
    log.info("Starting image generation...")
    img_path = None # 初始化 img_path 变量
    try:
        template_path = resource_path("hymb.png")
        font_path = resource_path("FZSTK.TTF")
        temp_image_dir = PLUGIN_DIR / "temp_images"
        temp_image_dir.mkdir(exist_ok=True) # 如果临时目录不存在则创建它

        if not template_path.exists():
            log.error(f"Image template not found at: {template_path}")
            return None

        if font_path and not font_path.exists():
            log.warning(f"Font file not found at: {font_path}. Falling back to default font.")
            font_path = None

        img = Image.open(template_path).convert('RGB')
        draw = ImageDraw.Draw(img)

        batch_number = data.get('v0', 'N/A')
        order_id_api = data.get('v2', 'N/A')

        content_map = [
            (data.get('v0', 'N/A'), (1490, 1030), 80),
            (data.get('v1', 'N/A'), (1450, 1300), 80),
            (order_id_api,         (1100, 1570), 80),
            (data.get('v3', 'N/A'), (1440, 1850), 80),
            (data.get('v4', 'N/A'), (1000, 2110), 80),
            (data.get('v5', 'N/A'), (1440, 2380), 80),
        ]

        # 绘制文本
        for text, position, font_size in content_map:
            try:
                if font_path:
                    font = ImageFont.truetype(str(font_path), font_size)
                else:
                     font = ImageFont.load_default()
            except Exception as e:
                log.warning(f"Could not load specified font for text '{text}': {e}. Using default.")
                try:
                     font = ImageFont.load_default()
                except Exception as fallback_e:
                     log.error(f"Failed to load default font for text: {fallback_e}. Cannot draw text.")
                     continue # Skip drawing this text

            try:
                 draw.text(position, str(text), font=font, fill=(0, 0, 0))
            except Exception as draw_e:
                 log.error(f"Failed to draw text '{text}' at position {position}: {draw_e}")


        # 添加生成时间戳
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        timestamp_position = (1210, 2680)
        timestamp_font_size = 80
        try:
            if font_path:
                timestamp_font = ImageFont.truetype(str(font_path), timestamp_font_size)
            else:
                timestamp_font = ImageFont.load_default()
        except Exception as e:
             log.warning(f"Could not load specified font for timestamp: {e}. Using default.")
             try:
                 timestamp_font = ImageFont.load_default()
             except Exception as fallback_e:
                 log.error(f"Failed to load default font for timestamp: {fallback_e}. Cannot draw timestamp.")
                 timestamp_font = None

        if timestamp_font:
            try:
                draw.text(timestamp_position, timestamp, font=timestamp_font, fill=(0, 0, 0))
            except Exception as draw_e:
                 log.error(f"Failed to draw timestamp at position {timestamp_position}: {draw_e}")


        # 临时保存图片
        safe_batch = batch_number.lstrip('#').replace('/', '_').replace('\\', '_').replace(' ', '_')
        if not safe_batch or safe_batch.isspace():
             safe_batch = "UnknownBatch"
        current_timestamp = time.strftime('%Y%m%d%H%M%S_%f') # 添加微秒，提高唯一性
        img_filename = f"Heyao_{safe_batch}_{current_timestamp}.png"
        img_path = temp_image_dir / img_filename

        img.save(img_path, format='PNG', quality=95)
        log.info(f"Generated image saved successfully to: {img_path}")
        return img_path

    except FileNotFoundError as e:
        log.error(f"Error finding required file for image generation: {e}")
        return None
    except Exception as e:
        log.error(f"Error generating image: {e}", exc_info=True)
        return None
    # 暂时保留不清理，用于测试
    # finally:
    #     log.info("Image generation function finished.")

# 注册插件
@register(
    name="heyao_query",
    desc="河妖订单查询插件",
    version="1.0.3", # 更新版本号以区分改动
    author="Your Name"
)
class HeyaoQueryStar(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        log.info("HeyaoQueryStar plugin initialized.")
        # >>>>>> 添加：用于存储上一张图片的路径 <<<<<<
        self.last_image_path: Optional[Path] = None
        log.info("Initialized last_image_path attribute.")


    @filter.command("heyao", alias={"河妖", "查订单"})
    async def handle_heyao_query(self, event: AstrMessageEvent):

        log.info("handle_heyao_query started.")
        full_message = event.get_message_str()
        log.debug(f"Received full message: {full_message}")
        parts = full_message.split(maxsplit=1)

        if len(parts) < 2 or not parts[1].strip():
            log.warning("No order ID provided.")
            yield event.plain_result("请提供订单号。用法：/heyao <订单号>")
            log.info("handle_heyao_query finished (no order ID).")
            return

        order_id_user = parts[1].strip()

        if not order_id_user:
            log.warning("Order ID is empty after stripping.")
            yield event.plain_result("订单号不能为空。用法：/heyao <订单号>")
            log.info("handle_heyao_query finished (empty order ID).")
            return

        log.info(f"Received query for order: {order_id_user}")
        yield event.plain_result(f"正在查询订单号：{order_id_user}...")
        log.info(f"Sent initial query message for order {order_id_user}.")

        api_data = await fetch_wechat_info(order_id_user)

        if api_data is None:
            log.error(f"API data fetch failed or returned invalid data for order: {order_id_user}")
            yield event.plain_result(f"查询订单 {order_id_user} 时出错，请检查日志或稍后再试。")
            log.info("handle_heyao_query finished (API fetch failed).")
            return

        try:
            log.debug(f"Raw API data received: {api_data}")
            query_data_list = api_data.get('queryDataList')

            if not query_data_list or not isinstance(query_data_list, list) or len(query_data_list) == 0:
                log.warning(f"API response for {order_id_user} has no 'queryDataList' or it is empty/invalid type. Data: {api_data}")
                error_msg = api_data.get('msg', '未找到订单信息或API返回格式不正确。')
                if error_msg == '未找到订单信息或API返回格式不正确。':
                     if api_data.get('code') == -1:
                         error_msg = f"未找到订单 {order_id_user} 的信息。"
                     elif 'error' in api_data:
                          error_msg = f"API错误: {api_data['error']}"
                error_msg = str(error_msg)
                yield event.plain_result(f"查询失败：{error_msg} (订单号: {order_id_user})")
                log.info("handle_heyao_query finished (API data invalid or empty).")
                return

            order_details = query_data_list[0].get('content')
            if not order_details or not isinstance(order_details, dict):
                 log.warning(f"First item in 'queryDataList' for {order_id_user} is missing 'content' or 'content' is not a dictionary. Data: {query_data_list[0]}")
                 yield event.plain_result(f"查询成功，但未能解析订单详细信息。(订单号: {order_id_user})")
                 log.info("handle_heyao_query finished (API content invalid).")
                 return

            log.info(f"Successfully parsed data for order {order_id_user}: {order_details}")

        except Exception as e: # 简化捕获，包含KeyError, IndexError, TypeError等
            log.error(f"Error processing API response for order {order_id_user}: {e}", exc_info=True)
            log.debug(f"API Response Data: {api_data}")
            yield event.plain_result(f"处理API响应时发生错误。(订单号: {order_id_user})")
            log.info("handle_heyao_query finished (API processing error).")
            return

        # >>>>>> 添加：删除上一张图片的逻辑 <<<<<<
        # 在生成新图片之前，先检查并删除上一次生成的图片
        if self.last_image_path and self.last_image_path.exists():
            log.info(f"Attempting to delete previous image: {self.last_image_path}")
            try:
                self.last_image_path.unlink() # 删除文件
                log.info(f"Successfully deleted previous image: {self.last_image_path}")
            except OSError as e:
                # 如果删除失败（例如文件被占用），记录错误但不中断流程
                log.error(f"Failed to delete previous image {self.last_image_path}: {e}")
            except Exception as e:
                log.error(f"An unexpected error occurred while deleting {self.last_image_path}: {e}", exc_info=True)
        # >>>>>> 删除逻辑结束 <<<<<<

        # 生成图片
        log.info("Calling generate_image function...")
        image_path = generate_image(order_details)
        log.info(f"generate_image returned path: {image_path}")

        if image_path and image_path.exists():
            log.info(f"Image generated successfully and file exists at: {image_path}. Attempting to yield for sending.")

            # >>>>>> 添加：更新上一张图片的路径为当前生成的图片路径 <<<<<<
            self.last_image_path = image_path
            log.info(f"Updated last_image_path to: {self.last_image_path}")
            # >>>>>> 更新路径结束 <<<<<<

            try:
                # 将参数名从 path 改为 file，这里原来就是对的，无需改动
                chain = [
                    ImageComp(file=str(image_path.absolute())) # 使用 ImageComp，参数名是 file
                ]
                # 这个 yield 只是将组件对象返回给框架，实际发送是异步的
                yield event.chain_result(chain)
                log.info(f"Image component yielded successfully for order {order_id_user}. Framework will handle sending.")

            except Exception as e:
                # 这个 except 捕获到了创建 ImageComp 对象时的错误
                log.error(f"Caught exception during ImageComp creation or yield process for image {image_path}: {e}", exc_info=True)
                yield event.plain_result("生成图片成功，但发送时遇到问题。")

            # 清理临时文件的逻辑现在由插件内部处理，不需要额外的 finally 块或外部工具。

        else:
            log.error(f"Image generation failed or file not found for order {order_id_user}. Returned path: {image_path}")
            # 如果新的图片生成失败，则不更新 self.last_image_path，保留上一个成功的图片路径（如果存在）
            yield event.plain_result(f"成功获取订单信息，但在生成图片时失败。(订单号: {order_id_user})")
            log.info("handle_heyao_query finished (image generation failed).")

        log.info("handle_heyao_query finished.") # 添加日志：函数结束
