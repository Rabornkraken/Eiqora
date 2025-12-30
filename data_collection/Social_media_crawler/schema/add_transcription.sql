
-- Add transcription column to douyin_aweme
ALTER TABLE `douyin_aweme` ADD COLUMN `transcription` LONGTEXT DEFAULT NULL COMMENT '视频语音转文字内容';
