-- Add tracking fields for automated user profile regeneration
ALTER TABLE user_personalization_profiles
ADD COLUMN IF NOT EXISTS data_fed_count INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS profile_update_count INTEGER NOT NULL DEFAULT 0;
