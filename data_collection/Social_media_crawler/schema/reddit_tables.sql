
-- ----------------------------
-- Table structure for reddit_post
-- Reddit帖子表
-- ----------------------------
DROP TABLE IF EXISTS `reddit_post`;
CREATE TABLE `reddit_post` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '自增ID',
  `post_id` varchar(64) NOT NULL COMMENT '帖子ID',
  `subreddit` varchar(64) NOT NULL COMMENT 'Subreddit',
  `title` varchar(500) DEFAULT NULL COMMENT '帖子标题',
  `selftext` LONGTEXT COMMENT '帖子正文',
  `author` varchar(64) DEFAULT NULL COMMENT '作者',
  `score` int DEFAULT 0 COMMENT '分数',
  `upvote_ratio` float DEFAULT 0.0 COMMENT '点赞率',
  `num_comments` int DEFAULT 0 COMMENT '评论数',
  `created_utc` bigint DEFAULT NULL COMMENT '发布时间戳',
  `url` varchar(512) DEFAULT NULL COMMENT '帖子链接',
  `create_time` bigint DEFAULT NULL COMMENT '抓取时间戳',
  `last_modify_ts` bigint DEFAULT NULL COMMENT '最后修改时间戳',
  `topic_id` varchar(64) DEFAULT NULL COMMENT '关联的话题ID',
  `crawling_task_id` varchar(64) DEFAULT NULL COMMENT '关联的爬取任务ID',
  `source_keyword` varchar(255) DEFAULT '' COMMENT '搜索来源关键字',
  PRIMARY KEY (`id`),
  UNIQUE KEY `idx_reddit_post_id` (`post_id`),
  KEY `idx_reddit_subreddit` (`subreddit`),
  KEY `idx_reddit_created_utc` (`created_utc`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='Reddit帖子表';

-- ----------------------------
-- Table structure for reddit_comment
-- Reddit评论表
-- ----------------------------
DROP TABLE IF EXISTS `reddit_comment`;
CREATE TABLE `reddit_comment` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '自增ID',
  `comment_id` varchar(64) NOT NULL COMMENT '评论ID',
  `post_id` varchar(64) NOT NULL COMMENT '帖子ID',
  `parent_id` varchar(64) DEFAULT NULL COMMENT '父评论ID',
  `author` varchar(64) DEFAULT NULL COMMENT '作者',
  `body` LONGTEXT COMMENT '评论内容',
  `score` int DEFAULT 0 COMMENT '分数',
  `created_utc` bigint DEFAULT NULL COMMENT '发布时间戳',
  `create_time` bigint DEFAULT NULL COMMENT '抓取时间戳',
  `last_modify_ts` bigint DEFAULT NULL COMMENT '最后修改时间戳',
  PRIMARY KEY (`id`),
  UNIQUE KEY `idx_reddit_comment_id` (`comment_id`),
  KEY `idx_reddit_comment_post_id` (`post_id`),
  KEY `idx_reddit_comment_created_utc` (`created_utc`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='Reddit评论表';
