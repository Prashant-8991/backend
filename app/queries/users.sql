-- Users table for Google OAuth + Role-Based Access Control
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    google_id VARCHAR(255) UNIQUE NOT NULL,
    picture VARCHAR(500),
    role VARCHAR(20) NOT NULL DEFAULT 'viewer' CHECK (role IN ('admin', 'manager', 'viewer')),
    is_approved BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login TIMESTAMP WITH TIME ZONE
);

-- Insert default admin user if not exists (using a known Google account)
-- This admin must be created after the first Google login by an admin manually via DB
-- Or you can seed one manually:
-- INSERT INTO users (email, name, google_id, role, is_approved, is_active)
-- VALUES ('admin@example.com', 'Admin User', 'ADMIN_GOOGLE_ID_HERE', 'admin', TRUE, TRUE);
