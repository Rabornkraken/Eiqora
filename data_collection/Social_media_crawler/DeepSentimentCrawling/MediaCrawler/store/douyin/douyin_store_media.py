import pathlib
from typing import Dict
import os
import aiofiles

from base.base_crawler import AbstractStoreImage, AbstractStoreVideo
from tools import utils
from tools.transcriber import VideoTranscriber
from db import db_conn_pool_var

class DouYinImage(AbstractStoreImage):
    image_store_path: str = "data/douyin/images"

    async def store_image(self, image_content_item: Dict):
        await self.save_image(image_content_item.get("aweme_id"), image_content_item.get("pic_content"), image_content_item.get("extension_file_name"))

    def make_save_file_name(self, aweme_id: str, extension_file_name: str) -> str:
        return f"{self.image_store_path}/{aweme_id}/{extension_file_name}"

    async def save_image(self, aweme_id: str, pic_content: str, extension_file_name):
        pathlib.Path(self.image_store_path + "/" + aweme_id).mkdir(parents=True, exist_ok=True)
        save_file_name = self.make_save_file_name(aweme_id, extension_file_name)
        async with aiofiles.open(save_file_name, 'wb') as f:
            await f.write(pic_content)
            utils.logger.info(f"[DouYinImageStoreImplement.save_image] save image {save_file_name} success ...")


class DouYinVideo(AbstractStoreVideo):
    video_store_path: str = "data/douyin/videos"

    async def store_video(self, video_content_item: Dict):
        aweme_id = video_content_item.get("aweme_id")
        video_content = video_content_item.get("video_content")
        extension_file_name = video_content_item.get("extension_file_name")
        title = video_content_item.get("title", aweme_id)
        
        # 1. Save Video
        save_file_name = await self.save_video(aweme_id, title, video_content, extension_file_name)
        
        # 2. Transcribe Video
        if save_file_name:
            utils.logger.info(f"[DouYinVideo] Starting transcription for {save_file_name}")
            import asyncio
            loop = asyncio.get_running_loop()
            try:
                transcription = await loop.run_in_executor(
                    None, 
                    VideoTranscriber.transcribe_video, 
                    save_file_name
                )
                
                # 3. Update Database
                if transcription:
                    utils.logger.info(f"[DouYinVideo] Transcription success, length: {len(transcription)}")
                    await self.update_db_transcription(aweme_id, transcription)
                else:
                    utils.logger.info(f"[DouYinVideo] No transcription generated (empty result)")
                
                # 4. Clean up video file
                try:
                    os.remove(save_file_name)
                    utils.logger.info(f"[DouYinVideo] Deleted temporary video file: {save_file_name}")
                    # Try to remove the directory if empty
                    video_dir = os.path.dirname(save_file_name)
                    os.rmdir(video_dir)
                    utils.logger.info(f"[DouYinVideo] Deleted empty video directory: {video_dir}")
                except Exception as e:
                    utils.logger.warning(f"[DouYinVideo] Failed to cleanup video file: {e}")
                    
            except Exception as e:
                utils.logger.error(f"[DouYinVideo] Transcription exception: {e}")

    def sanitize_filename(self, name: str) -> str:
        import re
        # Remove invalid characters
        name = re.sub(r'[\\/*?:"<>|]', "", name)
        # Remove newlines
        name = name.replace("\n", "").replace("\r", "")
        # Truncate to reasonable length (e.g. 50 chars) to avoid path length issues
        if len(name) > 50:
            name = name[:50]
        return name.strip()

    def make_save_file_name(self, aweme_id: str, title: str, extension_file_name: str) -> str:
        safe_title = self.sanitize_filename(title)
        # Format: data/douyin/videos/{safe_title}_{aweme_id}/{safe_title}.mp4
        folder_name = f"{safe_title}_{aweme_id}"
        return f"{self.video_store_path}/{folder_name}/{safe_title}.mp4"

    async def save_video(self, aweme_id: str, title: str, video_content: str, extension_file_name) -> str:
        try:
            safe_title = self.sanitize_filename(title)
            folder_name = f"{safe_title}_{aweme_id}"
            folder_path = f"{self.video_store_path}/{folder_name}"
            
            pathlib.Path(folder_path).mkdir(parents=True, exist_ok=True)
            
            # Update save_file_name logic
            save_file_name = f"{folder_path}/{safe_title}.mp4"
            
            async with aiofiles.open(save_file_name, 'wb') as f:
                await f.write(video_content)
                utils.logger.info(f"[DouYinVideoStoreImplement.save_video] save video {save_file_name} success ...")
            return save_file_name
        except Exception as e:
            utils.logger.error(f"[DouYinVideo] Save video failed: {e}")
            return ""

    async def update_db_transcription(self, aweme_id: str, text: str):
        """Update the transcription field in database"""
        try:
            pool = db_conn_pool_var.get()
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    sql = "UPDATE douyin_aweme SET transcription = %s WHERE aweme_id = %s"
                    await cursor.execute(sql, (text, aweme_id))
                    await conn.commit()
                    utils.logger.info(f"[DouYinVideo] DB Updated transcription for {aweme_id}")
        except Exception as e:
            utils.logger.error(f"[DouYinVideo] DB Update failed: {e}")