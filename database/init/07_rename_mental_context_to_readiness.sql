-- Add mental_readiness_averages column to user_personalization_profiles
ALTER TABLE user_personalization_profiles
ADD COLUMN IF NOT EXISTS mental_readiness_averages JSONB;
