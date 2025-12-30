
-- ----------------------------
-- Table structure for youtube_video
-- YouTube视频表
-- ----------------------------
DROP TABLE IF EXISTS `youtube_video`;
CREATE TABLE `youtube_video` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '自增ID',
  `video_id` varchar(64) NOT NULL COMMENT '视频ID',
  `title` varchar(500) DEFAULT NULL COMMENT '视频标题',
  `desc` text COMMENT '视频描述',
  `channel_id` varchar(64) DEFAULT NULL COMMENT '频道ID',
  `channel_name` varchar(128) DEFAULT NULL COMMENT '频道名称',
  `view_count` varchar(32) DEFAULT NULL COMMENT '观看次数',
  `publish_time` varchar(32) DEFAULT NULL COMMENT '发布时间',
  `duration` varchar(16) DEFAULT NULL COMMENT '时长',
  `url` varchar(512) DEFAULT NULL COMMENT '视频链接',
  `transcription` LONGTEXT DEFAULT NULL COMMENT '视频字幕/转录',
  `create_time` bigint DEFAULT NULL COMMENT '抓取时间戳',
  `last_modify_ts` bigint DEFAULT NULL COMMENT '最后修改时间戳',
  PRIMARY KEY (`id`),
  UNIQUE KEY `idx_youtube_video_id` (`video_id`),
  KEY `idx_youtube_channel_id` (`channel_id`),
  KEY `idx_youtube_create_time` (`create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='YouTube视频表';
