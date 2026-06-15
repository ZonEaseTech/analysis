CREATE TABLE `runs` (
	`id` text PRIMARY KEY NOT NULL,
	`script_id` text NOT NULL,
	`script_name` text NOT NULL,
	`script_path` text NOT NULL,
	`args` text,
	`started_at` text NOT NULL,
	`finished_at` text,
	`exit_code` integer,
	`status` text NOT NULL,
	`log` text
);
