
-- ----------------------------
-- Table structure for xueqiu_note
-- 雪球帖子表
-- ----------------------------
DROP TABLE IF EXISTS `xueqiu_note`;
CREATE TABLE `xueqiu_note` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '自增ID',
  `note_id` varchar(64) NOT NULL COMMENT '帖子ID',
  `user_id` varchar(64) DEFAULT NULL COMMENT '用户ID',
  `nickname` varchar(64) DEFAULT NULL COMMENT '用户昵称',
  `title` varchar(500) DEFAULT NULL COMMENT '帖子标题',
  `desc` text COMMENT '帖子内容',
  `liked_count` varchar(16) DEFAULT NULL COMMENT '点赞数',
  `collected_count` varchar(16) DEFAULT NULL COMMENT '收藏数',
  `comment_count` varchar(16) DEFAULT NULL COMMENT '评论数',
  `share_count` varchar(16) DEFAULT NULL COMMENT '分享数',
  `posted_time` varchar(32) DEFAULT NULL COMMENT '发布时间',
  `url` varchar(512) DEFAULT NULL COMMENT '帖子链接',
  `create_time` bigint DEFAULT NULL COMMENT '抓取时间戳',
  `last_modify_ts` bigint DEFAULT NULL COMMENT '最后修改时间戳',
  `topic_id` varchar(64) DEFAULT NULL COMMENT '关联的话题ID',
  `crawling_task_id` varchar(64) DEFAULT NULL COMMENT '关联的爬取任务ID',
  PRIMARY KEY (`id`),
  UNIQUE KEY `idx_xueqiu_note_id` (`note_id`),
  KEY `idx_xueqiu_note_user_id` (`user_id`),
  KEY `idx_xueqiu_note_create_time` (`create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='雪球帖子表';

-- ----------------------------
-- Table structure for xueqiu_note_comment
-- 雪球评论表
-- ----------------------------
DROP TABLE IF EXISTS `xueqiu_note_comment`;
CREATE TABLE `xueqiu_note_comment` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '自增ID',
  `comment_id` varchar(64) NOT NULL COMMENT '评论ID',
  `note_id` varchar(64) NOT NULL COMMENT '帖子ID',
  `user_id` varchar(64) DEFAULT NULL COMMENT '用户ID',
  `nickname` varchar(64) DEFAULT NULL COMMENT '用户昵称',
  `content` text COMMENT '评论内容',
  `liked_count` varchar(16) DEFAULT NULL COMMENT '点赞数',
  `create_time` bigint DEFAULT NULL COMMENT '抓取时间戳',
  `last_modify_ts` bigint DEFAULT NULL COMMENT '最后修改时间戳',
  PRIMARY KEY (`id`),
  UNIQUE KEY `idx_xueqiu_comment_id` (`comment_id`),
  KEY `idx_xueqiu_comment_note_id` (`note_id`),
  KEY `idx_xueqiu_comment_create_time` (`create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='雪球评论表';
