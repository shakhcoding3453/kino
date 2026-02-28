import logging
import asyncio
import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
import random
import string

from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.types import BotCommand, Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters.state import StateFilter
from aiogram.filters import Command
from dotenv import load_dotenv
from math import ceil
import json

# load environment variables
load_dotenv()

# === configuration ===
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
ADMIN_IDS = list(map(int, os.getenv('ADMIN_IDS', '').split(','))) if os.getenv('ADMIN_IDS') else []
CONTENT_CHANNEL_ID = int(os.getenv('CONTENT_CHANNEL_ID', 0))
DB_PATH = 'data/bot.db'
VIP_PRICE_UZS = 30000
VIP_PRICE_STARS = 300
SPAM_LIMIT = 3  # max requests
SPAM_WINDOW = 10  # seconds
AD_COOLDOWN = 120  # seconds between ads for same user
ITEMS_PER_PAGE = 5

# === database class ===
class Database:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        """Initialize database tables"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Admins table
        cursor.execute('''CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            added_by INTEGER
        )''')

        # Users table
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            vip_until TIMESTAMP,
            is_banned INTEGER DEFAULT 0,
            last_action_ts TIMESTAMP,
            action_counter INTEGER DEFAULT 0
        )''')

        # Settings table
        cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            content_channel_id INTEGER,
            ads_every_n INTEGER DEFAULT 3,
            force_sub_enabled INTEGER DEFAULT 1,
            vip_price_uzs INTEGER DEFAULT 30000,
            vip_price_stars INTEGER DEFAULT 300
        )''')

        # Categories table
        cursor.execute('''CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0
        )''')

        # Contents table
        cursor.execute('''CREATE TABLE IF NOT EXISTS contents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            year INTEGER,
            description TEXT,
            category_id INTEGER,
            poster_file_id TEXT,
            video_file_id TEXT,
            video_url TEXT,
            is_vip_only INTEGER DEFAULT 0,
            is_published INTEGER DEFAULT 1,
            channel_message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            views_count INTEGER DEFAULT 0,
            FOREIGN KEY(category_id) REFERENCES categories(id)
        )''')

        # Favorites table
        cursor.execute('''CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER,
            content_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, content_id),
            FOREIGN KEY(user_id) REFERENCES users(user_id),
            FOREIGN KEY(content_id) REFERENCES contents(id)
        )''')

        # Search logs table
        cursor.execute('''CREATE TABLE IF NOT EXISTS search_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            query_text TEXT,
            found_count INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )''')

        # Ads table
        cursor.execute('''CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            type TEXT,
            payload TEXT,
            button_text TEXT,
            button_url TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        # Ad events table
        cursor.execute('''CREATE TABLE IF NOT EXISTS ad_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ad_id INTEGER,
            user_id INTEGER,
            event_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(ad_id) REFERENCES ads(id),
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )''')

        # Force channels table
        cursor.execute('''CREATE TABLE IF NOT EXISTS force_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id_or_username TEXT UNIQUE NOT NULL,
            invite_link TEXT,
            is_active INTEGER DEFAULT 1
        )''')

        # VIP Settings table
        cursor.execute('''CREATE TABLE IF NOT EXISTS vip_settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            uzs_price INTEGER DEFAULT 30000,
            stars_price INTEGER DEFAULT 300,
            stars_account_id TEXT,
            payment_method TEXT DEFAULT 'both'
        )''')

        # Payments table
        cursor.execute('''CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount_uzs INTEGER,
            stars_amount INTEGER,
            payment_type TEXT,
            status TEXT DEFAULT 'pending',
            screenshot_file_id TEXT,
            card_number TEXT,
            transaction_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_by INTEGER,
            approved_at TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )''')

        # Insert default category if not exists
        cursor.execute("INSERT OR IGNORE INTO categories (name, is_active, sort_order) VALUES (?, 1, 0)", ("🎬 Kino",))

        # Insert default settings if not exists
        cursor.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")

        conn.commit()
        conn.close()

    # ===== ADMIN METHODS =====
    def add_admin(self, user_id: int, added_by: int) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)", (user_id, added_by))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def remove_admin(self, user_id: int) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def is_admin(self, user_id: int) -> bool:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        result = cursor.fetchone() is not None
        conn.close()
        return result

    def get_all_admins(self) -> List[int]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM admins")
        admins = [row[0] for row in cursor.fetchall()]
        conn.close()
        return admins

    # ===== USER METHODS =====
    def register_user(self, user_id: int) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()

    def ban_user(self, user_id: int) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def unban_user(self, user_id: int) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def is_user_banned(self, user_id: int) -> bool:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return row[0] == 1 if row else False

    def is_vip(self, user_id: int) -> bool:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT vip_until FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return False
        if row[0] is None:
            return False
        return datetime.fromisoformat(row[0]) > datetime.now()

    def set_vip(self, user_id: int, days: int = 30) -> None:
        self.register_user(user_id)
        vip_until = datetime.now() + timedelta(days=days)
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET vip_until = ? WHERE user_id = ?", (vip_until.isoformat(), user_id))
        conn.commit()
        conn.close()

    def get_vip_users(self) -> List[Dict]:
        """Return list of current VIP users with vip_until timestamp"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, vip_until FROM users WHERE vip_until IS NOT NULL AND vip_until > datetime('now')")
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def remove_vip(self, user_id: int) -> bool:
        """Remove VIP status from a user"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET vip_until = NULL WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def get_vip_until(self, user_id: int) -> Optional[str]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT vip_until FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row and row[0] else None

    # ===== CATEGORY METHODS =====
    def add_category(self, name: str) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO categories (name, is_active) VALUES (?, 1)", (name,))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def get_all_categories(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, is_active, sort_order FROM categories WHERE is_active = 1 ORDER BY sort_order")
        categories = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return categories

    def get_category_by_name(self, name: str) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, is_active FROM categories WHERE name = ? AND is_active = 1", (name,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def remove_category(self, category_id: int) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE categories SET is_active = 0 WHERE id = ?", (category_id,))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    # ===== CONTENT METHODS =====
    def generate_content_code(self) -> str:
        """Generate unique content code like K123"""
        conn = self.get_connection()
        cursor = conn.cursor()
        while True:
            code = 'K' + ''.join(random.choices(string.digits, k=6))
            cursor.execute("SELECT 1 FROM contents WHERE code = ?", (code,))
            if cursor.fetchone() is None:
                conn.close()
                return code

    def add_content(self, title: str, category_id: int, year: int = None, description: str = None,
                   poster_file_id: str = None, video_file_id: str = None, video_url: str = None,
                   is_vip_only: bool = False) -> Tuple[bool, Optional[str]]:
        try:
            code = self.generate_content_code()
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''INSERT INTO contents 
                (code, title, category_id, year, description, poster_file_id, video_file_id, video_url, is_vip_only)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (code, title, category_id, year, description, poster_file_id, video_file_id, video_url, 1 if is_vip_only else 0))
            conn.commit()
            conn.close()
            return True, code
        except Exception as e:
            return False, None

    def get_content_by_code(self, code: str) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''SELECT id, code, title, year, description, category_id, poster_file_id, 
                        video_file_id, video_url, is_vip_only, is_published, channel_message_id, 
                        created_at, views_count FROM contents WHERE code = ?''', (code,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_content_by_id(self, content_id: int) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''SELECT id, code, title, year, description, category_id, poster_file_id, 
                        video_file_id, video_url, is_vip_only, is_published, channel_message_id, 
                        created_at, views_count FROM contents WHERE id = ?''', (content_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def search_content(self, query: str, limit: int = 50) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''SELECT id, code, title, year, description, is_vip_only FROM contents 
                        WHERE (title LIKE ? OR code LIKE ?) AND is_published = 1
                        ORDER BY created_at DESC LIMIT ?''',
                      (f'%{query}%', f'%{query}%', limit))
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def get_latest_content(self, category_id: int = None, limit: int = 50) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        if category_id:
            cursor.execute('''SELECT id, code, title, year, is_vip_only FROM contents 
                            WHERE category_id = ? AND is_published = 1
                            ORDER BY created_at DESC LIMIT ?''', (category_id, limit))
        else:
            cursor.execute('''SELECT id, code, title, year, is_vip_only FROM contents 
                            WHERE is_published = 1
                            ORDER BY created_at DESC LIMIT ?''', (limit,))
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def get_top_content(self, category_id: int = None, limit: int = 20, days: int = 7) -> List[Dict]:
        """Get top content by views in last N days"""
        conn = self.get_connection()
        cursor = conn.cursor()
        date_threshold = datetime.now() - timedelta(days=days)
        if category_id:
            cursor.execute('''SELECT id, code, title, year, views_count, is_vip_only FROM contents 
                            WHERE category_id = ? AND is_published = 1 AND created_at > ?
                            ORDER BY views_count DESC LIMIT ?''',
                          (category_id, date_threshold.isoformat(), limit))
        else:
            cursor.execute('''SELECT id, code, title, year, views_count, is_vip_only FROM contents 
                            WHERE is_published = 1 AND created_at > ?
                            ORDER BY views_count DESC LIMIT ?''',
                          (date_threshold.isoformat(), limit))
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def delete_content(self, content_id: int) -> Tuple[bool, Optional[int]]:
        """Delete content and related rows. Returns (success, channel_message_id or None)"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT channel_message_id FROM contents WHERE id = ?", (content_id,))
            row = cursor.fetchone()
            channel_msg_id = row['channel_message_id'] if row and 'channel_message_id' in row.keys() else None

            # delete favorites referencing this content
            cursor.execute("DELETE FROM favorites WHERE content_id = ?", (content_id,))

            # delete the content row
            cursor.execute("DELETE FROM contents WHERE id = ?", (content_id,))
            conn.commit()
            conn.close()
            return True, channel_msg_id
        except Exception:
            return False, None

    def increment_views(self, content_id: int) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE contents SET views_count = views_count + 1 WHERE id = ?", (content_id,))
        conn.commit()
        conn.close()

    def publish_content(self, code: str, channel_message_id: int) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE contents SET channel_message_id = ? WHERE code = ?", (channel_message_id, code))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def hide_content(self, content_id: int) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE contents SET is_published = 0 WHERE id = ?", (content_id,))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def show_content(self, content_id: int) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE contents SET is_published = 1 WHERE id = ?", (content_id,))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def get_all_content(self, include_hidden: bool = False) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        if include_hidden:
            cursor.execute('''SELECT id, code, title, year, is_published, is_vip_only FROM contents ORDER BY created_at DESC''')
        else:
            cursor.execute('''SELECT id, code, title, year, is_published, is_vip_only FROM contents WHERE is_published = 1 ORDER BY created_at DESC''')
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    # ===== FAVORITES METHODS =====
    def add_favorite(self, user_id: int, content_id: int) -> bool:
        try:
            self.register_user(user_id)
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO favorites (user_id, content_id) VALUES (?, ?)", (user_id, content_id))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def remove_favorite(self, user_id: int, content_id: int) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM favorites WHERE user_id = ? AND content_id = ?", (user_id, content_id))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def is_favorite(self, user_id: int, content_id: int) -> bool:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM favorites WHERE user_id = ? AND content_id = ?", (user_id, content_id))
        result = cursor.fetchone() is not None
        conn.close()
        return result

    def get_user_favorites(self, user_id: int, limit: int = 50) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''SELECT c.id, c.code, c.title, c.year, c.is_vip_only FROM contents c
                        INNER JOIN favorites f ON c.id = f.content_id
                        WHERE f.user_id = ? ORDER BY f.created_at DESC LIMIT ?''',
                      (user_id, limit))
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    # ===== SEARCH LOG METHODS =====
    def log_search(self, user_id: int, query: str, found_count: int) -> None:
        self.register_user(user_id)
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO search_logs (user_id, query_text, found_count) VALUES (?, ?, ?)",
                      (user_id, query, found_count))
        conn.commit()
        conn.close()

    def get_top_searches(self, limit: int = 10) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''SELECT query_text, COUNT(*) as count FROM search_logs 
                        GROUP BY query_text ORDER BY count DESC LIMIT ?''', (limit,))
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def get_not_found_searches(self, limit: int = 10) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''SELECT query_text, COUNT(*) as count FROM search_logs 
                        WHERE found_count = 0 GROUP BY query_text ORDER BY count DESC LIMIT ?''', (limit,))
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    # ===== ADS METHODS =====
    def add_ad(self, title: str, type_: str, payload: str, button_text: str, button_url: str) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''INSERT INTO ads (title, type, payload, button_text, button_url, is_active)
                            VALUES (?, ?, ?, ?, ?, 1)''',
                          (title, type_, payload, button_text, button_url))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def get_active_ads(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, type, payload, button_text, button_url FROM ads WHERE is_active = 1")
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def get_all_ads(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, type, payload, button_text, button_url, is_active FROM ads ORDER BY created_at DESC")
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def toggle_ad(self, ad_id: int) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE ads SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?", (ad_id,))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def delete_ad(self, ad_id: int) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM ads WHERE id = ?", (ad_id,))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def log_ad_event(self, ad_id: int, user_id: int, event_type: str) -> None:
        self.register_user(user_id)
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO ad_events (ad_id, user_id, event_type) VALUES (?, ?, ?)",
                      (ad_id, user_id, event_type))
        conn.commit()
        conn.close()

    def get_ad_stats(self, ad_id: int = None, days: int = 7) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        date_threshold = datetime.now() - timedelta(days=days)
        
        if ad_id:
            cursor.execute('''SELECT ad_id, 
                            SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END) as impressions,
                            SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END) as clicks
                            FROM ad_events WHERE ad_id = ? AND created_at > ?
                            GROUP BY ad_id''', (ad_id, date_threshold.isoformat()))
        else:
            cursor.execute('''SELECT ad_id, 
                            SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END) as impressions,
                            SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END) as clicks
                            FROM ad_events WHERE created_at > ?
                            GROUP BY ad_id ORDER BY clicks DESC''', (date_threshold.isoformat(),))
        
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    # ===== FORCE CHANNELS METHODS =====
    def add_force_channel(self, chat_id_or_username: str, invite_link: str = None) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO force_channels (chat_id_or_username, invite_link, is_active) VALUES (?, ?, 1)",
                          (chat_id_or_username, invite_link))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def get_force_channels(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, chat_id_or_username, invite_link FROM force_channels WHERE is_active = 1")
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def remove_force_channel(self, channel_id: int) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE force_channels SET is_active = 0 WHERE id = ?", (channel_id,))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    # ===== SETTINGS METHODS =====
    def get_settings(self) -> Dict:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''SELECT content_channel_id, ads_every_n, force_sub_enabled, vip_price_uzs, vip_price_stars FROM settings WHERE id = 1''')
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else {}

    def update_setting(self, key: str, value) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(f"UPDATE settings SET {key} = ? WHERE id = 1", (value,))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def set_content_channel(self, channel_id: int) -> bool:
        return self.update_setting('content_channel_id', channel_id)

    def set_ads_frequency(self, every_n: int) -> bool:
        return self.update_setting('ads_every_n', every_n)

    def update_content_field(self, content_id: int, field: str, value) -> bool:
        """Update a single field in content"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if field == 'is_vip_only':
                value = 1 if value else 0
            cursor.execute(f"UPDATE contents SET {field} = ? WHERE id = ?", (value, content_id))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def get_all_users(self, include_banned: bool = False) -> List[Dict]:
        """Get all registered users"""
        conn = self.get_connection()
        cursor = conn.cursor()
        if include_banned:
            cursor.execute("SELECT user_id, joined_at, is_banned FROM users")
        else:
            cursor.execute("SELECT user_id, joined_at, is_banned FROM users WHERE is_banned = 0")
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def update_category_name(self, category_id: int, new_name: str) -> bool:
        """Update category name"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, category_id))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    # ===== ADS METHODS =====
    def add_ad(self, title: str, button_text: str, button_url: str) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''INSERT INTO ads (title, button_text, button_url, is_active) 
                            VALUES (?, ?, ?, 1)''', (title, button_text, button_url))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def get_active_ads(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, button_text, button_url FROM ads WHERE is_active = 1")
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def log_ad_click(self, ad_id: int, user_id: int) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''INSERT INTO ad_events (ad_id, user_id, event_type) 
                            VALUES (?, ?, 'click')''', (ad_id, user_id))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def get_ad_stats(self, ad_id: int) -> Dict:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as clicks FROM ad_events WHERE ad_id = ? AND event_type = 'click'", (ad_id,))
        result = cursor.fetchone()
        conn.close()
        return dict(result) if result else {"clicks": 0}

    # ===== VIP SETTINGS METHODS =====
    def get_vip_settings(self) -> Dict:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM vip_settings WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else {
            "uzs_price": 30000,
            "stars_price": 300,
            "stars_account_id": "",
            "payment_method": "both"
        }

    def update_vip_settings(self, uzs_price: int = None, stars_price: int = None, 
                           stars_account_id: str = None, payment_method: str = None) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if uzs_price:
                cursor.execute("UPDATE vip_settings SET uzs_price = ? WHERE id = 1", (uzs_price,))
            if stars_price:
                cursor.execute("UPDATE vip_settings SET stars_price = ? WHERE id = 1", (stars_price,))
            if stars_account_id:
                cursor.execute("UPDATE vip_settings SET stars_account_id = ? WHERE id = 1", (stars_account_id,))
            if payment_method:
                cursor.execute("UPDATE vip_settings SET payment_method = ? WHERE id = 1", (payment_method,))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    # ===== PAYMENT METHODS =====
    def create_payment(self, user_id: int, amount_uzs: int = 0, stars_amount: int = 0,
                      payment_type: str = "uzs") -> Optional[int]:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''INSERT INTO payments (user_id, amount_uzs, stars_amount, payment_type, status)
                            VALUES (?, ?, ?, ?, 'pending')''',
                          (user_id, amount_uzs, stars_amount, payment_type))
            payment_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return payment_id
        except:
            return None

    def get_payment(self, payment_id: int) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM payments WHERE id = ?", (payment_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def update_payment_screenshot(self, payment_id: int, file_id: str) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE payments SET screenshot_file_id = ? WHERE id = ?", (file_id, payment_id))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def approve_payment(self, payment_id: int, admin_id: int) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            payment = self.get_payment(payment_id)
            if payment:
                user_id = payment['user_id']
                self.set_vip(user_id, days=30)
            cursor.execute("UPDATE payments SET status = 'approved', approved_by = ?, approved_at = datetime('now') WHERE id = ?",
                          (admin_id, payment_id))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def reject_payment(self, payment_id: int, admin_id: int) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE payments SET status = 'rejected', approved_by = ?, approved_at = datetime('now') WHERE id = ?",
                          (admin_id, payment_id))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    def get_pending_payments(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM payments WHERE status = 'pending' ORDER BY created_at DESC")
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

# ===== ROUTER & KEYBOARDS =====
router = Router()

def main_menu_kb(user_id=None, db=None):
    buttons = [
        [KeyboardButton(text="🔎 Qidiruv"), KeyboardButton(text="📂 Kategoriyalar")],
        [KeyboardButton(text="🆕 Yangi qo'shilgan"), KeyboardButton(text="🔥 Top")],
        [KeyboardButton(text="👤 Profil"), KeyboardButton(text="💎 VIP / Obuna")],
    ]
    
    # Add admin button only for admins
    if user_id and db and (check_admin(user_id, db)):
        buttons.append([KeyboardButton(text="👨‍💻 Admin Paneli")])
    
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def back_kb(back_to):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data=back_to)]])

def content_detail_kb(code, user_id, db):
    content = db.get_content_by_code(code)
    is_favorite = db.is_favorite(user_id, content['id'])
    fav_text = "⭐ Sevimlilar (olib tashlash)" if is_favorite else "⭐ Sevimlilarga qo'shish"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Ko'rish", callback_data=f"watch:{code}")],
        [InlineKeyboardButton(text=fav_text, callback_data=f"favorite:{code}")],
        [InlineKeyboardButton(text="⬅️ Menyuga", callback_data="main_menu")]
    ])

def channel_post_kb(code):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="▶️ Ko'rish", callback_data=f"open:{code}")]])

def categories_kb(categories):
    buttons = []
    for cat in categories:
        buttons.append([InlineKeyboardButton(text=cat['name'], callback_data=f"cat:{cat['id']}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def category_menu_kb(category_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Yangi", callback_data=f"cat_new:{category_id}")],
        [InlineKeyboardButton(text="🔥 Trend", callback_data=f"cat_top:{category_id}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="categories")]
    ])

def pagination_kb(page, total_pages, callback_prefix, back_to="main_menu"):
    buttons = []
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"{callback_prefix}:{page-1}"))
    nav_buttons.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"{callback_prefix}:{page+1}"))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data=back_to)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def content_list_kb(contents, page, total_pages, callback_prefix, back_to="main_menu"):
    buttons = []
    for content in contents:
        btn_text = f"🎬 {content['title']}"
        if content.get('is_vip_only'):
            btn_text += " 💎"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"view:{content['code']}")])
    
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"{callback_prefix}:{page-1}"))
    nav_buttons.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"{callback_prefix}:{page+1}"))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data=back_to)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def profile_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Sevimlilar", callback_data="favorites")],
        [InlineKeyboardButton(text="💎 VIP holatim", callback_data="vip_status")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")]
    ])

def vip_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 30 000 so'm uchun", callback_data="vip_uzs")],
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data="vip_stars")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")]
    ])

def force_subscribe_kb(channels):
    buttons = []
    for ch in channels:
        link = ch.get('invite_link', f"https://t.me/{ch['chat_id_or_username'].lstrip('@')}")
        buttons.append([InlineKeyboardButton(text=f"📢 {ch['chat_id_or_username']}", url=link)])
    buttons.append([InlineKeyboardButton(text="✅ Obuna bo'ldim", callback_data="check_subscribe")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Kontent qo'shish", callback_data="admin_add_content")],
        [InlineKeyboardButton(text="✏️ Tahrirlash", callback_data="admin_edit_content")],
        [InlineKeyboardButton(text="🗂 Kategoriyalar", callback_data="admin_categories")],
        [InlineKeyboardButton(text="💰 Reklama", callback_data="admin_ads")],
        [InlineKeyboardButton(text="📈 Analytics", callback_data="admin_analytics")],
        [InlineKeyboardButton(text="✅ Obuna", callback_data="admin_force_channels")],
        [InlineKeyboardButton(text="💎 VIP narxlari", callback_data="admin_vip_settings")],
        [InlineKeyboardButton(text="👮 Adminlar", callback_data="admin_admins")],
        [InlineKeyboardButton(text="🗄️ Backup", callback_data="admin_backup")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")]
    ])

# provide kb object for compatibility
class _Keyboards:
    pass

kb = _Keyboards()
for fn in [main_menu_kb, back_kb, content_detail_kb, channel_post_kb, categories_kb, category_menu_kb, pagination_kb, content_list_kb, profile_kb, vip_menu_kb, force_subscribe_kb, admin_menu_kb]:
    setattr(kb, fn.__name__, fn)

# ===== HELPER FUNCTIONS =====
def check_admin(user_id, db):
    return db.is_admin(user_id) or user_id in ADMIN_IDS

def check_banned(user_id, db):
    return db.is_user_banned(user_id)

# ===== STATES =====
class ContentStates(StatesGroup):
    cat = State()
    name = State()
    year = State()
    desc = State()
    poster = State()
    vip = State()
    video = State()

class AdminStates(StatesGroup):
    category = State()
    admin_id = State()
    # content addition states
    content_code = State()
    content_title = State()
    content_year = State()
    content_image = State()
    content_description = State()
    # advertisement states
    ad_title = State()
    ad_button = State()
    ad_url = State()
    ad_preview = State()
    ad_schedule = State()
    force_ch = State()
    force_link = State()

# The rest of the handlers follow exactly as in handlers.py previously.

async def check_force_subscribe(user_id: int, bot, db: Database) -> bool:
    """Check if user is subscribed to all force channels"""
    settings = db.get_settings()
    if not settings.get('force_sub_enabled'):
        return True
    
    channels = db.get_force_channels()
    if not channels:
        return True
    
    for channel in channels:
        try:
            # URL'ni username ga o'zgartiramiz
            chat_id = channel['chat_id_or_username']
            
            # Agar URL bo'lsa (https://t.me/username), username olish
            if isinstance(chat_id, str) and chat_id.startswith('http'):
                # https://t.me/Kino_kodlari_uz2 -> @Kino_kodlari_uz2
                chat_id = '@' + chat_id.split('/')[-1]
            elif isinstance(chat_id, str) and not chat_id.startswith('@'):
                # username -> @username
                chat_id = '@' + chat_id
            
            logger.info(f"Tekshiruv: user {user_id} - kanal {chat_id}")
            
            member = await bot.get_chat_member(chat_id, user_id)
            logger.info(f"User {user_id} status: {member.status} in {chat_id}")
            
            # Agar user left yoki kicked bo'lsa, obuna qilmagani
            if member.status in ['left', 'kicked']:
                logger.info(f"User {user_id} obuna qilmagani - status: {member.status}")
                return False
                
        except Exception as e:
            logger.error(f"Force subscribe tekshiruvda xato: {e}")
            # Xato bo'lsa ham True qaytaramiz (bot o'zi kanalga kirmasa ham)
            continue
    
    return True

# ===== START & MAIN MENU =====
@router.message(Command("start"))
async def start_handler(message: Message, state: FSMContext, db: Database, bot):
    user_id = message.from_user.id
    db.register_user(user_id)
    
    if check_banned(user_id, db):
        await message.answer("❌ Siz ban qilingansiz!")
        return
    
    # Check force subscribe
    if not await check_force_subscribe(user_id, bot, db):
        channels = db.get_force_channels()
        text = "📢 Avval kanallarga obuna bo'ling:\n\n"
        await message.answer(text, reply_markup=kb.force_subscribe_kb(channels))
        return
    
    await state.clear()
    text = "🎬 Kino Hubga xush kelibsiz!\n\nTugmalar orqali tanlang:"
    await message.answer(text, reply_markup=kb.main_menu_kb(user_id, db))

@router.message(F.text == "✅ Obuna")
async def manual_subscribe_check(message: Message, state: FSMContext, db: Database, bot: Bot):
    user_id = message.from_user.id
    # if admin, show manage panel
    if check_admin(user_id, db):
        # reuse admin_force_channels logic but for message
        channels = db.get_force_channels()
        text = f"✅ Majburiy obuna kanalları ({len(channels)} ta)\n\n"
        for ch in channels:
            text += f"📢 {ch['chat_id_or_username']}\n"
        buttons = [
            [InlineKeyboardButton(text="➕ Qo'shish", callback_data="add_force_ch")],
            [InlineKeyboardButton(text="❌ O'chirish", callback_data="admin_remove_force")],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")]
        ]
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return
    # non-admin user flow
    if not await check_force_subscribe(user_id, bot, db):
        channels = db.get_force_channels()
        text = "📢 Avval kanallarga obuna bo'ling:\n\n"
        await message.answer(text, reply_markup=kb.force_subscribe_kb(channels))
    else:
        await message.answer("✅ Siz hammasiga obuna bo'lgansiz!", reply_markup=kb.main_menu_kb(user_id, db))

@router.message(F.text == "✅ Obuna")
async def manual_subscribe_check(message: Message, state: FSMContext, db: Database, bot: Bot):
    user_id = message.from_user.id
    # if admin, show management panel
    if check_admin(user_id, db):
        channels = db.get_force_channels()
        text = f"✅ Majburiy obuna kanalları ({len(channels)} ta)\n\n"
        for ch in channels:
            text += f"📢 {ch['chat_id_or_username']}\n"
        buttons = [
            [InlineKeyboardButton(text="➕ Qo'shish", callback_data="add_force_ch")],
            [InlineKeyboardButton(text="❌ O'chirish", callback_data="admin_remove_force")],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")]
        ]
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return
    # non-admin flow: check subscription
    if not await check_force_subscribe(user_id, bot, db):
        channels = db.get_force_channels()
        text = "📢 Avval kanallarga obuna bo'ling:\n\n"
        await message.answer(text, reply_markup=kb.force_subscribe_kb(channels))
    else:
        await message.answer("✅ Siz hammasiga obuna bo'lgansiz!", reply_markup=kb.main_menu_kb(user_id, db))

@router.callback_query(F.data == "main_menu")
async def main_menu_callback(query: CallbackQuery, state: FSMContext, db: Database, bot: Bot):
    await state.clear()
    
    if check_banned(query.from_user.id, db):
        await query.answer("❌ Siz ban qilingansiz!")
        return
    
    if not await check_force_subscribe(query.from_user.id, bot, db):
        channels = db.get_force_channels()
        text = "📢 Avval kanallarga obuna bo'ling:\n\n"
        await query.message.edit_text(text, reply_markup=kb.force_subscribe_kb(channels))
        return
    
    text = "🎬 Kino Hubga xush kelibsiz!\n\nTugmalar orqali tanlang:"
    await query.answer()
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))

# ===== SEARCH =====
@router.message(F.text == "🔎 Qidiruv")
async def search_menu_msg(message: Message, state: FSMContext):
    await state.clear()
    text = "🔎 Film nomini yoki kodini yuboring\n(masalan: K123 yoki 'Avengers')"
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")]]))
    await state.set_state("waiting_search")

@router.callback_query(F.data == "search")
async def search_menu(query: CallbackQuery, state: FSMContext):
    await state.clear()
    text = "🔎 Film nomini yoki kodini yuboring\n(masalan: K123 yoki 'Avengers')"
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")]]))
    await state.set_state("waiting_search")

@router.message(F.text, StateFilter("waiting_search"))
async def search_handler(message: Message, state: FSMContext, db: Database):
    user_id = message.from_user.id
    query_text = message.text
    
    if check_banned(user_id, db):
        await message.answer("❌ Siz ban qilingansiz!")
        return
    
    # Search content
    results = db.search_content(query_text, limit=50)
    db.log_search(user_id, query_text, len(results))
    
    if not results:
        text = f"❌ '{query_text}' bo'yicha topilmadi\n\n💡 So'rovingiz adminlarga yuborildi."
        await message.answer(text, reply_markup=kb.back_kb("main_menu"))
        await state.clear()
        return
    
    # Show first page
    await state.clear()
    await state.set_state("viewing_search_results")
    await state.update_data(search_results=results, current_page=1)
    
    page = 1
    page_size = ITEMS_PER_PAGE
    total_pages = ceil(len(results) / page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = results[start_idx:end_idx]
    
    text = f"🔎 Topilgan: {len(results)} ta\n\n"
    for content in page_results:
        status = " 💎" if content.get('is_vip_only') else ""
        text += f"🎬 {content['title']}{status}\n"
    
    await message.answer(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "search_page", "main_menu"))

# ===== CATEGORIES =====
@router.message(F.text == "📂 Kategoriyalar")
async def categories_menu_msg(message: Message, state: FSMContext, db: Database):
    await state.clear()
    categories = db.get_all_categories()
    text = "📂 Kategoriyalarni tanlang:"
    
    # Arrange buttons in 2 per row
    buttons = []
    for i in range(0, len(categories), 2):
        row = []
        row.append(KeyboardButton(text=categories[i]['name']))
        if i + 1 < len(categories):
            row.append(KeyboardButton(text=categories[i+1]['name']))
        buttons.append(row)
    
    buttons.append([KeyboardButton(text="⬅️ Orqaga")])
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("waiting_category_select")

@router.message(StateFilter("waiting_category_select"))
async def category_selected_msg(message: Message, state: FSMContext, db: Database):
    if message.text == "⬅️ Orqaga":
        await state.clear()
        user_id = message.from_user.id
        text = "🎬 Kino Hubga xush kelibsiz!\n\nTugmalar orqali tanlang:"
        await message.answer(text, reply_markup=main_menu_kb(user_id, db))
        return
    
    category_name = message.text
    category = db.get_category_by_name(category_name)
    
    if not category:
        await message.answer("❌ Kategoriya topilmadi")
        return
    
    # Ask for content code
    text = f"📂 {category_name}\n\n🔍 Kino kodi yoki nomini kiritingplease:\n\nYoki /back bosing"
    await state.update_data(selected_category_id=category['id'], selected_category_name=category_name)
    await state.set_state("waiting_category_search")
    await message.answer(text)

@router.message(StateFilter("waiting_category_search"))
async def category_search(message: Message, state: FSMContext, db: Database):
    if message.text == "/back":
        await state.clear()
        categories = db.get_all_categories()
        text = "📂 Kategoriyalarni tanlang:"
        buttons = []
        for i in range(0, len(categories), 2):
            row = []
            row.append(KeyboardButton(text=categories[i]['name']))
            if i + 1 < len(categories):
                row.append(KeyboardButton(text=categories[i+1]['name']))
            buttons.append(row)
        buttons.append([KeyboardButton(text="⬅️ Orqaga")])
        await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
        await state.set_state("waiting_category_select")
        return
    
    data = await state.get_data()
    category_id = data.get("selected_category_id")
    
    # Search by code first, then by name
    query_text = message.text.strip()
    results = db.search_content(query_text, limit=50)
    results = [r for r in results if r['category_id'] == category_id]
    
    if not results:
        await message.answer(f"❌ '{query_text}' topilmadi bu kategoriyada")
        return
    
    await state.clear()
    await state.set_state("viewing_category_search")
    await state.update_data(contents=results, current_page=1)
    
    page = 1
    page_size = ITEMS_PER_PAGE
    total_pages = ceil(len(results) / page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = results[start_idx:end_idx]
    
    text = f"📂 Natijalar ({len(results)} ta):"
    await message.answer(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "cat_search_page", "main_menu"))

@router.message(F.text == "⬅️ Orqaga")
async def back_to_main(message: Message, state: FSMContext, db: Database):
    current_state = await state.get_state()
    user_id = message.from_user.id
    
    # Handle going back from different sections
    if current_state == "viewing_favorites" or current_state == "profile":
        # Back from profile to main menu
        await state.clear()
        text = "🎬 Kino Hubga xush kelibsiz!\n\nTugmalar orqali tanlang:"
        await message.answer(text, reply_markup=main_menu_kb(user_id, db))
    elif current_state == "admin_menu":
        # Back from root admin menu
        await state.clear()
        text = "🎬 Kino Hubga xush kelibsiz!\n\nTugmalar orqali tanlang:"
        await message.answer(text, reply_markup=main_menu_kb(user_id, db))
    else:
        await state.clear()
        text = "🎬 Kino Hubga xush kelibsiz!\n\nTugmalar orqali tanlang:"
        await message.answer(text, reply_markup=main_menu_kb(user_id, db))

@router.message(F.text == "👤 Profil")
async def profile_msg(message: Message, db: Database):
    user_id = message.from_user.id
    is_vip = db.is_vip(user_id)
    favorites = db.get_user_favorites(user_id, limit=100)
    
    vip_status = "✅ Faol" if is_vip else "❌ Faol emas"
    fav_count = len(favorites)
    
    text = f"👤 Profil\n\n"
    text += f"💎 VIP: {vip_status}\n"
    text += f"⭐ Sevimlilar: {fav_count} ta\n"
    
    buttons = [
        [KeyboardButton(text="⭐ Sevimlilarni ko'rish" if fav_count > 0 else "⭐ Sevimlilar (0)")],
        [KeyboardButton(text="⬅️ Orqaga")]
    ]
    
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))

@router.message(F.text == "⭐ Sevimlilarni ko'rish")
async def view_favorites_msg(message: Message, state: FSMContext, db: Database):
    user_id = message.from_user.id
    favorites = db.get_user_favorites(user_id, limit=50)
    
    if not favorites:
        await message.answer("❌ Sevimlilar bo'sh")
        return
    
    await state.set_state("viewing_favorites")
    await state.update_data(contents=favorites, current_page=1)
    
    page = 1
    page_size = ITEMS_PER_PAGE
    total_pages = ceil(len(favorites) / page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = favorites[start_idx:end_idx]
    
    text = f"⭐ Sevimlilar ({len(favorites)} ta):"
    await message.answer(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "fav_page", "profile"))

@router.message(F.text == "⭐ Sevimlilar (0)")
async def view_favorites_empty(message: Message):
    await message.answer("❌ Sevimlilar bo'sh")

@router.message(F.text == "💎 VIP / Obuna")
async def vip_menu_msg(message: Message, state: FSMContext, db: Database):
    user_id = message.from_user.id
    settings = db.get_vip_settings()
    price_uzs = settings.get('uzs_price', 30000)
    price_stars = settings.get('stars_price', 300)
    payment_method = settings.get('payment_method', 'both')
    
    text = f"💎 VIP Obunalik (30 kun)\n\n"
    
    if payment_method in ['uzs', 'both']:
        text += f"💵 So'm: {price_uzs:,}\n"
    if payment_method in ['stars', 'both']:
        text += f"⭐ Stars: {price_stars}\n"
    
    text += "\n❓ Tolov usulini tanlang:"
    
    buttons = []
    if payment_method in ['uzs', 'both']:
        buttons.append([KeyboardButton(text="💵 So'm bilan to'lash")])
    if payment_method in ['stars', 'both']:
        buttons.append([KeyboardButton(text="⭐ Stars bilan to'lash")])
    buttons.append([KeyboardButton(text="⬅️ Orqaga")])
    
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("vip_payment_method")

@router.message(F.text == "💵 So'm bilan to'lash", StateFilter("vip_payment_method"))
async def vip_pay_uzs(message: Message, state: FSMContext, db: Database):
    user_id = message.from_user.id
    settings = db.get_vip_settings()
    price_uzs = settings.get('uzs_price', 30000)
    
    # Create payment record
    payment_id = db.create_payment(user_id, amount_uzs=price_uzs, payment_type='uzs')
    
    if not payment_id:
        await message.answer("❌ Xato yuz berdi!")
        await state.clear()
        return
    
    text = f"💵 So'm Bilan To'lash\n\n"
    text += f"💰 Summa: {price_uzs:,} so'm\n"
    text += f"📋 To'lov ID: {payment_id}\n\n"
    text += "Quyida ko'rsatilgan karta raqamiga pul o'tkazing va screenshotini yuboring:"
    
    buttons = [[KeyboardButton(text="📸 Screenshot yuborish")], [KeyboardButton(text="❌ Bekor qilish")]]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.update_data(payment_id=payment_id, payment_method='uzs')
    await state.set_state("vip_send_screenshot")

@router.message(F.text == "⭐ Stars bilan to'lash", StateFilter("vip_payment_method"))
async def vip_pay_stars(message: Message, state: FSMContext, db: Database):
    # temporarily disable stars payments
    await message.answer("🚧 Stars bilan to'lash hozircha mavjud emas, tez orada yangilanadi.")
    await state.clear()
    # show vip menu again so user can choose another option
    await vip_menu_msg(message, state, db)

@router.message(F.text == "📸 Screenshot yuborish", StateFilter("vip_send_screenshot"))
async def vip_send_screenshot(message: Message, state: FSMContext):
    text = "📸 To'lov screenshotini yuboring (rasm):"
    await message.answer(text)
    await state.set_state("vip_screenshot_input")

@router.message(StateFilter("vip_screenshot_input"))
async def vip_screenshot_input_handler(message: Message, state: FSMContext, db: Database, bot: Bot):
    if not message.photo:
        await message.answer("❌ Rasm yuboring!")
        return
    
    data = await state.get_data()
    payment_id = data.get("payment_id")
    payment_method = data.get("payment_method")
    
    file_id = message.photo[-1].file_id
    if db.update_payment_screenshot(payment_id, file_id):
        await message.answer("✅ Screenshot qabul qilindi! Admin tez orada tasdiqlaydi.")
        
        # Send to admins
        admins = db.get_all_admins()
        for admin_id in admins:
            try:
                admin_text = f"📝 Yangi VIP To'lov:\n\n"
                admin_text += f"👤 Foydalanuvchi: {message.from_user.id}\n"
                admin_text += f"📋 To'lov ID: {payment_id}\n"
                admin_text += f"💳 Usul: {'💵 So\'m' if payment_method == 'uzs' else '⭐ Stars'}\n"
                admin_text += f"\n🔘 Quyidagi tugmalardan birini bosing:\n"
                
                buttons = [
                    [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"approve_payment:{payment_id}")],
                    [InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_payment:{payment_id}")]
                ]
                
                await bot.send_photo(
                    chat_id=admin_id,
                    photo=file_id,
                    caption=admin_text,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
                )
            except:
                pass
        # show main menu for user
        user_id = message.from_user.id
        await message.answer("🎬 Kino Hubga xush kelibsiz!\n\nTugmalar orqali tanlang:", reply_markup=main_menu_kb(user_id, db))
    else:
        await message.answer("❌ Xato yuz berdi!")
    
    await state.clear()

@router.message(F.text == "❌ Bekor qilish", StateFilter("vip_send_screenshot"))
async def vip_cancel_payment(message: Message, state: FSMContext, db: Database):
    await message.answer("❌ To'lov bekor qilindi")
    await state.clear()
    user_id = message.from_user.id
    text = "🎬 Kino Hubga xush kelibsiz!\n\nTugmalar orqali tanlang:"
    await message.answer(text, reply_markup=main_menu_kb(user_id, db))

@router.message(F.text == "🆕 Yangi qo'shilgan")
async def new_content_msg(message: Message, state: FSMContext, db: Database):
    contents = db.get_latest_content(limit=5)  # Last 5 items
    
    if not contents:
        await message.answer("❌ Yangi kontent yo'q")
        return
    
    await state.set_state("viewing_new_content")
    await state.update_data(contents=contents, current_page=1)
    
    page = 1
    page_size = 5
    total_pages = 1
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = contents[start_idx:end_idx]
    
    text = f"🆕 Yangi qo'shilgan ({len(contents)} ta):"
    await message.answer(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "new_page", "main_menu"))

@router.message(F.text == "🔥 Top")
async def top_content_msg(message: Message, state: FSMContext, db: Database):
    contents = db.get_top_content(limit=50)  # Top most viewed/searched
    
    if not contents:
        await message.answer("❌ Top kontent yo'q")
        return
    
    await state.set_state("viewing_top_content")
    await state.update_data(contents=contents, current_page=1)
    
    page = 1
    page_size = ITEMS_PER_PAGE
    total_pages = ceil(len(contents) / page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = contents[start_idx:end_idx]
    
    text = f"🔥 Top kontentlar ({len(contents)} ta):"
    await message.answer(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "top_page", "main_menu"))

@router.message(F.text == "👨‍💻 Admin Paneli")
async def admin_menu_msg(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Siz admin emassiz!")
        return
    
    await state.clear()
    text = "👨‍💻 Admin Paneli:"
    buttons = [
        [KeyboardButton(text="➕ Kontent qo'shish"), KeyboardButton(text="✏️ Tahrirlash")],
        [KeyboardButton(text="🗂 Kategoriyalar"), KeyboardButton(text="💰 Reklama")],
        [KeyboardButton(text="📈 Analytics"), KeyboardButton(text="✅ Obuna")],
        [KeyboardButton(text="💎 VIP narxlari"), KeyboardButton(text="👮 Adminlar")],
        [KeyboardButton(text="🗄️ Backup")],
        [KeyboardButton(text="⬅️ Orqaga")]
    ]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_menu")

@router.message(F.text == "✅ Obuna", StateFilter("admin_menu"))
async def admin_menu_subscribe(message: Message, state: FSMContext, db: Database, bot: Bot):
    # simply reuse the callback logic but send new message
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    channels = db.get_force_channels()
    text = f"✅ Majburiy obuna kanalları ({len(channels)} ta)\n\n"
    for ch in channels:
        text += f"📢 {ch['chat_id_or_username']}\n"
    buttons = [
        [InlineKeyboardButton(text="➕ Qo'shish", callback_data="add_force_ch")],
        [InlineKeyboardButton(text="❌ O'chirish", callback_data="admin_remove_force")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")]
    ]
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.message(F.text == "✏️ Tahrirlash", StateFilter("admin_menu"))
async def admin_edit_msg(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    text = "✏️ Tahrirish uchun kino kodi yuboring:"
    await message.answer(text)
    await state.set_state("admin_edit_code")

@router.message(StateFilter("admin_edit_code"))
async def admin_edit_code_handler(message: Message, state: FSMContext, db: Database):
    code = message.text.strip().upper()
    content = db.get_content_by_code(code)
    
    if not content:
        await message.answer(f"❌ Kod '{code}' topilmadi")
        return
    
    text = f"🎬 {content['title']} (Kod: {code})\n\n❓ Qaysilarni tahrirlash kerak?\n\n"
    buttons = [
        [KeyboardButton(text="📸 Rasm"), KeyboardButton(text="📝 Izoh")],
        [KeyboardButton(text="🎬 Video"), KeyboardButton(text="🏷 Turi (VIP/Oddiy)")],
        [KeyboardButton(text="❌ O'chirish")],
        [KeyboardButton(text="⬅️ Orqaga")]
    ]
    
    await state.update_data(edit_content_code=code, edit_content_id=content['id'])
    await state.set_state("admin_edit_select")
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))

@router.message(StateFilter("admin_edit_select"))
async def admin_edit_select_handler(message: Message, state: FSMContext, db: Database):
    choice = message.text
    data = await state.get_data()
    content_id = data.get("edit_content_id")
    
    if choice == "⬅️ Orqaga":
        await state.clear()
        await message.answer("👨‍💻 Admin Paneli", reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="✏️ Tahrirlash")], [KeyboardButton(text="⬅️ Orqaga")]],
            resize_keyboard=True))
        return
    
    if choice == "📸 Rasm":
        await message.answer("📸 Yangi rasim yuboring:")
        await state.set_state("admin_edit_poster")
    elif choice == "📝 Izoh":
        await message.answer("📝 Yangi izoh yuboring:")
        await state.set_state("admin_edit_desc")
    elif choice == "🎬 Video":
        await message.answer("🎬 Yangi videoni yuboring:")
        await state.set_state("admin_edit_video")
    elif choice == "🏷 Turi (VIP/Oddiy)":
        buttons = [[KeyboardButton(text="🟢 Oddiy"), KeyboardButton(text="🔴 VIP")]]
        await message.answer("🏷 Kontentni tanlang:", reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
        await state.set_state("admin_edit_vip")
    elif choice == "❌ O'chirish":
        # ask for confirmation via inline buttons
        confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Ha, o'chirish", callback_data=f"confirm_delete:{content_id}")],
            [InlineKeyboardButton(text="❌ Bekor", callback_data="cancel_delete")]
        ])
        await message.answer("⚠️ E'tibor! Bu kinoni to'liq o'chirib tashlaydi. Tasdiqlaysizmi?", reply_markup=confirm_kb)
        return

@router.message(StateFilter("admin_edit_poster"))
async def admin_edit_poster_handler(message: Message, state: FSMContext, db: Database):
    if message.photo:
        data = await state.get_data()
        content_id = data.get("edit_content_id")
        file_id = message.photo[-1].file_id
        if db.update_content_field(content_id, 'poster_file_id', file_id):
            await message.answer("✅ Rasm o'zgartirildi!")
        else:
            await message.answer("❌ Xato yuz berdi!")
    else:
        await message.answer("❌ Rasm yuboring!")
        return
    
    await state.clear()
    text = "👨‍💻 Admin Paneli\n\nTanlang (2 ta qatorda):"
    buttons = [
        [KeyboardButton(text="➕ Kontent qo'shish"), KeyboardButton(text="✏️ Tahrirlash")],
        [KeyboardButton(text="🗂 Kategoriyalar"), KeyboardButton(text="💰 Reklama")],
        [KeyboardButton(text="📈 Analytics"), KeyboardButton(text="✅ Obuna")],
        [KeyboardButton(text="💎 VIP narxlari"), KeyboardButton(text="👮 Adminlar")],
        [KeyboardButton(text="🗄️ Backup")],
        [KeyboardButton(text="⬅️ Orqaga")]
    ]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_menu")

@router.message(StateFilter("admin_edit_desc"))
async def admin_edit_desc_handler(message: Message, state: FSMContext, db: Database):
    if message.text:
        data = await state.get_data()
        content_id = data.get("edit_content_id")
        if db.update_content_field(content_id, 'description', message.text):
            await message.answer("✅ Izoh o'zgartirildi!")
        else:
            await message.answer("❌ Xato yuz berdi!")
    else:
        await message.answer("❌ Tekst yuboring!")
        return
    
    await state.clear()
    text = "👨‍💻 Admin Paneli\n\nTanlang (2 ta qatorda):"
    buttons = [
        [KeyboardButton(text="➕ Kontent qo'shish"), KeyboardButton(text="✏️ Tahrirlash")],
        [KeyboardButton(text="🗂 Kategoriyalar"), KeyboardButton(text="💰 Reklama")],
        [KeyboardButton(text="📈 Analytics"), KeyboardButton(text="✅ Obuna")],
        [KeyboardButton(text="💎 VIP narxlari"), KeyboardButton(text="👮 Adminlar")],
        [KeyboardButton(text="🗄️ Backup")],
        [KeyboardButton(text="⬅️ Orqaga")]
    ]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_menu")

@router.message(StateFilter("admin_edit_video"))
async def admin_edit_video_handler(message: Message, state: FSMContext, db: Database):
    if message.video:
        data = await state.get_data()
        content_id = data.get("edit_content_id")
        file_id = message.video.file_id
        if db.update_content_field(content_id, 'video_file_id', file_id):
            await message.answer("✅ Video o'zgartirildi!")
        else:
            await message.answer("❌ Xato yuz berdi!")
    else:
        await message.answer("❌ Video yuboring!")
        return
    
    await state.clear()
    text = "👨‍💻 Admin Paneli\n\nTanlang (2 ta qatorda):"
    buttons = [
        [KeyboardButton(text="➕ Kontent qo'shish"), KeyboardButton(text="✏️ Tahrirlash")],
        [KeyboardButton(text="🗂 Kategoriyalar"), KeyboardButton(text="💰 Reklama")],
        [KeyboardButton(text="📈 Analytics"), KeyboardButton(text="✅ Obuna")],
        [KeyboardButton(text="💎 VIP narxlari"), KeyboardButton(text="👮 Adminlar")],
        [KeyboardButton(text="🗄️ Backup")],
        [KeyboardButton(text="⬅️ Orqaga")]
    ]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_menu")

@router.message(StateFilter("admin_edit_vip"))
async def admin_edit_vip_handler(message: Message, state: FSMContext, db: Database):
    if message.text == "🟢 Oddiy":
        is_vip = False
    elif message.text == "🔴 VIP":
        is_vip = True
    else:
        await message.answer("❌ Tugmalardan birini tanlang!")
        return
    
    data = await state.get_data()
    content_id = data.get("edit_content_id")
    if db.update_content_field(content_id, 'is_vip_only', 1 if is_vip else 0):
        await message.answer(f"✅ Tur {'VIP' if is_vip else 'Oddiy'} qilib o'zgartirildi!")
    else:
        await message.answer("❌ Xato yuz berdi!")
    
    await state.clear()
    text = "👨‍💻 Admin Paneli\n\nTanlang (2 ta qatorda):"
    buttons = [
        [KeyboardButton(text="➕ Kontent qo'shish"), KeyboardButton(text="✏️ Tahrirlash")],
        [KeyboardButton(text="🗂 Kategoriyalar"), KeyboardButton(text="💰 Reklama")],
        [KeyboardButton(text="📈 Analytics"), KeyboardButton(text="✅ Obuna")],
        [KeyboardButton(text="💎 VIP narxlari"), KeyboardButton(text="👮 Adminlar")],
        [KeyboardButton(text="🗄️ Backup")],
        [KeyboardButton(text="⬅️ Orqaga")]
    ]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_menu")



async def show_admin_categories_menu(message: Message, state: FSMContext):
    text = "🗂 Kategoriyalar\n\nNima qilmoqchisiz?"
    buttons = [
        [KeyboardButton(text="➕ Qo'shish"), KeyboardButton(text="✏️ O'zgartirish")],
        [KeyboardButton(text="❌ O'chirish"), KeyboardButton(text="👁 Ko'rish")],
        [KeyboardButton(text="⬅️ Orqaga")]
    ]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_categories")

@router.message(F.text == "🗂 Kategoriyalar", StateFilter("admin_menu"))
async def admin_categories_msg(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    text = "🗂 Kategoriyalar\n\nNima qilmoqchisiz?"
    buttons = [
        [KeyboardButton(text="➕ Qo'shish"), KeyboardButton(text="✏️ O'zgartirish")],
        [KeyboardButton(text="❌ O'chirish"), KeyboardButton(text="👁 Ko'rish")],
        [KeyboardButton(text="⬅️ Orqaga")]
    ]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_categories")

@router.message(F.text == "👁 Ko'rish", StateFilter("admin_categories"))
async def admin_categories_view(message: Message, state: FSMContext, db: Database):
    categories = db.get_all_categories()
    text = "🗂 Kategoriyalar:\n\n"
    for cat in categories:
        text += f"• {cat['name']}\n"
    
    buttons = [[KeyboardButton(text="⬅️ Orqaga")]]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_categories_view")

@router.message(F.text == "⬅️ Orqaga", StateFilter("admin_categories_view"))
async def admin_categories_view_back(message: Message, state: FSMContext):
    await show_admin_categories_menu(message, state)


@router.message(F.text == "➕ Qo'shish", StateFilter("admin_categories"))
async def admin_categories_add(message: Message, state: FSMContext):
    text = "🗂 Yangi kategoriya nomini yuboring:"
    buttons = [[KeyboardButton(text="⬅️ Orqaga")]]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_add_category")

@router.message(StateFilter("admin_add_category"))
async def admin_add_category_handler(message: Message, state: FSMContext, db: Database):
    if message.text == "⬅️ Orqaga":
        await show_admin_categories_menu(message, state)
        return
    name = message.text.strip()
    if db.add_category(name):
        await message.answer(f"✅ '{name}' kategoriya qo'shildi!")
    else:
        await message.answer("❌ Kategoriya qo'shishda xato yoki bu kategoriya mavjud!")
    await show_admin_categories_menu(message, state)


@router.message(StateFilter("admin_add_category"))
async def admin_add_category_handler(message: Message, state: FSMContext, db: Database):
    name = message.text.strip()
    if db.add_category(name):
        await message.answer(f"✅ '{name}' kategoriya qo'shildi!")
    else:
        await message.answer("❌ Kategoriya qo'shishda xato yoki bu kategoriya mavjud!")
    await state.clear()

@router.message(F.text == "❌ O'chirish", StateFilter("admin_categories"))
async def admin_categories_delete(message: Message, state: FSMContext, db: Database):
    categories = db.get_all_categories()
    text = "🗂 Qaysi kategoriyani o'chirish kerak?\n\n"
    buttons = []
    for cat in categories:
        buttons.append([KeyboardButton(text=cat['name'])])
    buttons.append([KeyboardButton(text="⬅️ Orqaga")])
    
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_delete_category")

@router.message(StateFilter("admin_delete_category"))
async def admin_delete_category_handler(message: Message, state: FSMContext, db: Database):
    if message.text == "⬅️ Orqaga":
        await show_admin_categories_menu(message, state)
        return
    
    category = db.get_category_by_name(message.text)
    if category and db.remove_category(category['id']):
        await message.answer(f"✅ '{message.text}' kategoriya o'chirildi!")
    else:
        await message.answer("❌ Kategoriya o'chirishda xato!")
    
    await show_admin_categories_menu(message, state)


@router.message(F.text == "✏️ O'zgartirish", StateFilter("admin_categories"))
async def admin_categories_edit(message: Message, state: FSMContext, db: Database):
    categories = db.get_all_categories()
    text = "🗂 Qaysi kategoriyani o'zgartirish kerak?\n\n"
    buttons = []
    for cat in categories:
        buttons.append([KeyboardButton(text=cat['name'])])
    buttons.append([KeyboardButton(text="⬅️ Orqaga")])
    
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_edit_category_select")

@router.message(StateFilter("admin_edit_category_select"))
async def admin_edit_category_select_handler(message: Message, state: FSMContext, db: Database):
    if message.text == "⬅️ Orqaga":
        await show_admin_categories_menu(message, state)
        return
    
    category = db.get_category_by_name(message.text)
    if not category:
        await message.answer("❌ Kategoriya topilmadi!")
        return
    
    text = f"Yangi nom yuboring (hozir: {message.text}):"
    await state.update_data(edit_category_id=category['id'], old_category_name=message.text)
    await state.set_state("admin_edit_category_name")
    await message.answer(text)

@router.message(StateFilter("admin_edit_category_name"))
async def admin_edit_category_name_handler(message: Message, state: FSMContext, db: Database):
    new_name = message.text.strip()
    data = await state.get_data()
    category_id = data.get("edit_category_id")
    
    if db.update_category_name(category_id, new_name):
        await message.answer(f"✅ Kategoriya nomi o'zgartirildi!")
    else:
        await message.answer("❌ O'zgartirishda xato!")
    
    await show_admin_categories_menu(message, state)

# ===== ADS MANAGEMENT =====
@router.message(F.text == "💰 Reklama", StateFilter("admin_menu"))
async def admin_ads_msg(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    text = "💰 Reklama Boshqaruvi\n\nNima qilmoqchisiz?"
    buttons = [
        [KeyboardButton(text="➕ Yangi reklama qo'shish")],
        [KeyboardButton(text="📊 Reklama statistikasi")],
        [KeyboardButton(text="⬅️ Orqaga")]
    ]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_ads_menu")

@router.message(F.text == "➕ Yangi reklama qo'shish", StateFilter("admin_ads_menu"))
async def admin_add_ads_msg(message: Message, state: FSMContext):
    text = "💰 Yangi reklama\n\n📝 Reklama sarlavhasini yuboring:"
    await message.answer(text)
    await state.set_state("admin_ad_title")

@router.message(StateFilter("admin_ad_title"))
async def admin_ad_title_handler(message: Message, state: FSMContext):
    title = message.text.strip()
    if not title:
        await message.answer("❌ Sarlavha bo'sh bo'lmasligi kerak!")
        return
    
    await state.update_data(ad_title=title)
    text = "📸 Reklama rasmi yuboring (ixtiyoriy, /skip bosing):"
    await message.answer(text)
    await state.set_state("admin_ad_image")

@router.message(StateFilter("admin_ad_image"))
async def admin_ad_image_handler(message: Message, state: FSMContext):
    if message.photo:
        file_id = message.photo[-1].file_id
        await state.update_data(ad_image_file_id=file_id)
    elif message.text == "/skip":
        await state.update_data(ad_image_file_id=None)
    else:
        await message.answer("❌ Rasm yuboring yoki /skip bosing!")
        return
    
    text = "📝 Reklama matnini yuboring:"
    await message.answer(text)
    await state.set_state("admin_ad_text")

@router.message(StateFilter("admin_ad_text"))
async def admin_ad_text_handler(message: Message, state: FSMContext):
    ad_text = message.text.strip()
    await state.update_data(ad_text=ad_text)
    
    text = "🔗 Tugma matni yuboring (masalan: 'Saytni ochish'):"
    await message.answer(text)
    await state.set_state("admin_ad_button_text")

@router.message(StateFilter("admin_ad_button_text"))
async def admin_ad_button_text_handler(message: Message, state: FSMContext):
    button_text = message.text.strip()
    await state.update_data(ad_button_text=button_text)
    
    text = "🔗 Tugma havolasini yuboring (URL yoki kanal @username):"
    await message.answer(text)
    await state.set_state("admin_ad_button_url")

@router.message(StateFilter("admin_ad_button_url"))
async def admin_ad_button_url_handler(message: Message, state: FSMContext):
    button_url = message.text.strip()
    await state.update_data(ad_button_url=button_url)
    data = await state.get_data()
    ad_title = data.get('ad_title')
    ad_text = data.get('ad_text','')
    button_text = data.get('ad_button_text')
    ad_image = data.get('ad_image_file_id')
    
    # preview
    caption = f"{ad_text}\n\n🔗 [{button_text}]({button_url})" if ad_text else f"🔗 [{button_text}]({button_url})"
    buttons = [[KeyboardButton(text="✅ Tasdiqlash"), KeyboardButton(text="❌ Bekor qilish")]]
    await message.answer("📝 Reklama tayyorlandi, tasdiqlashdan oldin tekshiring:", reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    if ad_image:
        await message.answer_photo(ad_image, caption=caption, parse_mode="Markdown")
    else:
        await message.answer(caption, parse_mode="Markdown")
    await state.set_state(AdminStates.ad_preview)

@router.message(StateFilter(AdminStates.ad_preview))
async def admin_ad_preview_response(message: Message, state: FSMContext, db: Database):
    text = message.text
    if text == "✅ Tasdiqlash":
        buttons = [
            [KeyboardButton(text="❗ Xozir")],
            [KeyboardButton(text="⏳ 30 minut")],
            [KeyboardButton(text="🕐 1 soat")],
            [KeyboardButton(text="⬅️ Orqaga")]
        ]
        await message.answer("⏰ Reklama qachon yuborilsin?", reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
        await state.set_state(AdminStates.ad_schedule)
    elif text == "❌ Bekor qilish":
        await message.answer("❌ Reklama bekor qilindi.")
        await state.clear()
        # return to ads menu
        await admin_ads_msg(message, state, message.bot.get('db'))
    else:
        await message.answer("❌ Iltimos, pastdagi tugmalardan birini tanlang!")

@router.message(StateFilter(AdminStates.ad_schedule))
async def admin_ad_schedule_handler(message: Message, state: FSMContext, db: Database, bot: Bot):
    choice = message.text
    delays = {"❗ Xozir": 0, "⏳ 30 minut": 1800, "🕐 1 soat": 3600}
    if choice not in delays and choice != "⬅️ Orqaga":
        await message.answer("❌ Tugmalardan birini tanlang!")
        return
    if choice == "⬅️ Orqaga":
        await state.set_state(AdminStates.ad_preview)
        await message.answer("🤞 Qaytadan tekshirish uchun oldingi tugmalarni bosing.")
        return
    delay = delays[choice]
    data = await state.get_data()
    db.add_ad(
        title=data['ad_title'],
        button_text=data['ad_button_text'],
        button_url=data['ad_button_url']
    )
    await state.clear()
    await message.answer(f"✅ Reklama rejalashtirildi ({choice}).")
    
    async def do_broadcast():
        users = db.get_all_users()
        caption = f"{data.get('ad_text','')}\n\n🔗 [{data['ad_button_text']}]({data['ad_button_url']})" if data.get('ad_text') else f"🔗 [{data['ad_button_text']}]({data['ad_button_url']})"
        for u in users:
            try:
                if data.get('ad_image_file_id'):
                    await bot.send_photo(u['user_id'], data['ad_image_file_id'], caption=caption, parse_mode="Markdown")
                else:
                    await bot.send_message(u['user_id'], caption, parse_mode="Markdown")
            except:
                pass
    if delay == 0:
        await do_broadcast()
    else:
        async def schedule_broadcast():
            await asyncio.sleep(delay)
            await do_broadcast()
        asyncio.create_task(schedule_broadcast())
    # after scheduling, return to ads menu
    await admin_ads_msg(message, state, db)

@router.message(F.text == "� Reklama statistikasi", StateFilter("admin_ads_menu"))
async def admin_ads_stats_msg(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    ad_stats = db.get_ad_stats()
    
    if not ad_stats:
        await message.answer("❌ Reklama yo'q")
        await state.set_state("admin_ads_menu")
        return
    
    text = "📊 Reklama Statistikasi:\n\n"
    for stat in ad_stats[:10]:
        impressions = stat['impressions'] or 0
        clicks = stat['clicks'] or 0
        ctr = (clicks / impressions * 100) if impressions > 0 else 0
        text += f"📢 Ad {stat['ad_id']}: {impressions} exp, {clicks} click, {ctr:.1f}% CTR\n"
    
    buttons = [[KeyboardButton(text="⬅️ Orqaga")]]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_ads_menu")

@router.message(F.text == "�📈 Analytics", StateFilter("admin_menu"))
async def admin_analytics_msg(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    users = db.get_all_users()
    admins = db.get_all_admins()
    contents = db.get_all_contents()
    top_searches = db.get_top_searches(5)
    not_found = db.get_not_found_searches(5)
    ad_stats = db.get_ad_stats()
    
    text = "📈 Statistika\n\n"
    text += f"👥 Foydalanuvchilar: {len(users)}\n"
    text += f"👨‍💼 Adminlar: {len(admins)}\n"
    text += f"🎬 Kontentlar: {len(contents)}\n"
    text += "\n🔍 Top qidiruvlar:\n"
    for search in top_searches:
        text += f"• {search['query_text']} ({search['count']} ta)\n"
    text += "\n❌ Topilmagan:\n"
    for search in not_found:
        text += f"• {search['query_text']} ({search['count']} ta)\n"
    text += "\n💰 Reklama Statistikasi:\n"
    for stat in ad_stats[:5]:
        impressions = stat['impressions'] or 0
        clicks = stat['clicks'] or 0
        ctr = (clicks / impressions * 100) if impressions > 0 else 0
        text += f"• Ad {stat['ad_id']}: {impressions} imp, {clicks} click, {ctr:.1f}% CTR\n"
    
    buttons = [[KeyboardButton(text="⬅️ Orqaga")]]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))

@router.message(F.text == "💎 VIP narxlari", StateFilter("admin_menu"))
async def admin_vip_prices_msg(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    settings = db.get_vip_settings()
    text = f"💎 VIP Narxlari Sozlamalari\n\n"
    text += f"💵 So'm: {settings.get('uzs_price', 30000):,}\n"
    text += f"⭐ Stars: {settings.get('stars_price', 300)}\n"
    text += f"🤖 Stars Akkaunt ID: {settings.get('stars_account_id', 'Ornatilmagan')}\n\n"
    text += "❓ Qaysilarni o'zgartirish kerak?"
    
    buttons = [
        [KeyboardButton(text="💵 So'm narxini o'zgartirish"), KeyboardButton(text="⭐ Stars narxini o'zgartirish")],
        [KeyboardButton(text="🤖 Stars Akkaunt ID"), KeyboardButton(text="📋 Tolov Usulini Tanlash")],
        [KeyboardButton(text="� VIP obunachilar"), KeyboardButton(text="�📝 Pending To'lovlar")],
        [KeyboardButton(text="⬅️ Orqaga")]
    ]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_vip_menu")

@router.message(F.text == "⬅️ Orqaga", StateFilter("admin_vip_menu"))
async def admin_vip_back(message: Message, state: FSMContext, db: Database):
    # go back to admin panel
    await state.clear()
    text = "👨‍💻 Admin Paneli\n\nTanlang:"
    await message.answer(text, reply_markup=kb.admin_menu_kb())

@router.message(F.text == "💵 So'm narxini o'zgartirish", StateFilter("admin_vip_menu"))
async def admin_change_uzs_price(message: Message, state: FSMContext):
    text = "💵 Yangi so'm narxini yuboring (raqam):"
    await message.answer(text)
    await state.set_state("admin_uzs_price")

@router.message(StateFilter("admin_uzs_price"))
async def admin_uzs_price_handler(message: Message, state: FSMContext, db: Database):
    try:
        price = int(message.text.strip())
        if db.update_vip_settings(uzs_price=price):
            await message.answer(f"✅ So'm narxi {price:,} qilib o'zgartirildi!")
        else:
            await message.answer("❌ Xato yuz berdi!")
    except:
        await message.answer("❌ Raqam yuboring!")
        return
    
    # show vip menu again
    await state.clear()
    await admin_vip_prices_msg(message, state, db)


@router.message(F.text == "⭐ Stars narxini o'zgartirish", StateFilter("admin_vip_menu"))
async def admin_change_stars_price(message: Message, state: FSMContext):
    text = "⭐ Yangi Stars narxini yuboring (raqam):"
    await message.answer(text)
    await state.set_state("admin_stars_price")

@router.message(StateFilter("admin_stars_price"))
async def admin_stars_price_handler(message: Message, state: FSMContext, db: Database):
    try:
        price = int(message.text.strip())
        if db.update_vip_settings(stars_price=price):
            await message.answer(f"✅ Stars narxi {price} qilib o'zgartirildi!")
        else:
            await message.answer("❌ Xato yuz berdi!")
    except:
        await message.answer("❌ Raqam yuboring!")
        return
    
    await state.clear()
    await admin_vip_prices_msg(message, state, db)


@router.message(F.text == "🤖 Stars Akkaunt ID", StateFilter("admin_vip_menu"))
async def admin_stars_account(message: Message, state: FSMContext):
    text = "🤖 Stars qabul qiladigan Telegram User ID yuboring:"
    await message.answer(text)
    await state.set_state("admin_stars_account_id")

@router.message(StateFilter("admin_stars_account_id"))
async def admin_stars_account_handler(message: Message, state: FSMContext, db: Database):
    account_id = message.text.strip()
    if db.update_vip_settings(stars_account_id=account_id):
        await message.answer(f"✅ Stars Akkaunt ID saqlab qo'yildi: {account_id}")
    else:
        await message.answer("❌ Xato yuz berdi!")
    
    await state.clear()
    await admin_vip_prices_msg(message, state, db)


@router.message(F.text == "📋 Tolov Usulini Tanlash", StateFilter("admin_vip_menu"))
async def admin_payment_method(message: Message, state: FSMContext):
    text = "📋 Tolov Usulini Tanlang:"
    buttons = [
        [KeyboardButton(text="💵 Faqat So'm"), KeyboardButton(text="⭐ Faqat Stars")],
        [KeyboardButton(text="📊 Ikkalasi Ham")]
    ]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_payment_method")

@router.message(F.text == "👥 VIP obunachilar", StateFilter("admin_vip_menu"))
async def admin_vip_subscribers(message: Message, state: FSMContext, db: Database):
    users = db.get_vip_users()
    if not users:
        await message.answer("✅ Hozirda VIP obunachilar yo'q")
        return
    for u in users:
        uid = u['user_id']
        vip_until = u.get('vip_until')
        try:
            expiry = datetime.fromisoformat(vip_until)
            remaining = expiry - datetime.now()
            days = remaining.days
            hours = remaining.seconds // 3600
            rem_text = f"{days} kun {hours} soat"
        except Exception:
            rem_text = vip_until or "-"
        text = f"👤 {uid}\n⏳ Qolgan: {rem_text}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ VIPni olib tashlash", callback_data=f"remove_vip:{uid}")]
        ])
        await message.answer(text, reply_markup=kb)

@router.message(StateFilter("admin_payment_method"))
async def admin_payment_method_handler(message: Message, state: FSMContext, db: Database):
    choice_map = {
        "💵 Faqat So'm": "uzs",
        "⭐ Faqat Stars": "stars",
        "📊 Ikkalasi Ham": "both"
    }
    
    method = choice_map.get(message.text)
    if method:
        if db.update_vip_settings(payment_method=method):
            await message.answer(f"✅ Tolov usuli {'So\'m' if method == 'uzs' else 'Stars' if method == 'stars' else 'Ikkalasi'} qilib o'zgartirildi!")
        else:
            await message.answer("❌ Xato yuz berdi!")
    else:
        await message.answer("❌ Tugmalardan birini tanlang!")
        return
    
    await state.clear()
    await admin_vip_prices_msg(message, state, db)

@router.message(F.text == "📝 Pending To'lovlar", StateFilter("admin_vip_menu"))
async def admin_pending_payments(message: Message, state: FSMContext, db: Database):
    payments = db.get_pending_payments()

    if not payments:
        await message.answer("✅ Pending to'lovlar yo'q")
        return

    # For each pending payment, show details and screenshot (if any) with inline approve/reject
    for p in payments:
        ptype = "💵 So'm" if p['payment_type'] == 'uzs' else "⭐ Stars"
        amount = p['amount_uzs'] if p['payment_type'] == 'uzs' else p.get('stars_amount')
        caption = f"📝 To'lov ID: {p['id']}\n👤 Foydalanuvchi: {p['user_id']}\n{ptype}: {amount}\n📅 {p['created_at']}"

        buttons = [
            [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"approve_payment:{p['id']}")],
            [InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_payment:{p['id']}")]
        ]

        # if screenshot available, send as photo with inline buttons; otherwise send text with buttons
        if p.get('screenshot_file_id'):
            try:
                await message.answer_photo(photo=p['screenshot_file_id'], caption=caption, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
            except:
                await message.answer(caption, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        else:
            await message.answer(caption, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    # final back button
    await message.answer("🔙 Orqaga", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Orqaga")]], resize_keyboard=True))

@router.message(F.text == "👮 Adminlar", StateFilter("admin_menu"))
async def admin_list_msg(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    admins = db.get_all_admins()
    text = f"👮 Adminlar ({len(admins)} ta):\n\n"
    for admin_id in admins:
        text += f"• {admin_id}\n"
    
    text += "\n❓ Nima qilmoqchisiz?"
    buttons = [
        [KeyboardButton(text="➕ Admin qo'shish"), KeyboardButton(text="❌ Admin o'chirish")],
        [KeyboardButton(text="⬅️ Orqaga")]
    ]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state("admin_admins_menu")

@router.message(F.text == "➕ Admin qo'shish", StateFilter("admin_admins_menu"))
async def admin_add_admin(message: Message, state: FSMContext):
    text = "➕ Yangi admin qo'shish\n\nAdmin User ID yuboring:"
    await message.answer(text)
    await state.set_state("admin_add_admin_id")

@router.message(StateFilter("admin_add_admin_id"))
async def admin_add_admin_handler(message: Message, state: FSMContext, db: Database):
    try:
        user_id = int(message.text.strip())
        if db.add_admin(user_id, message.from_user.id):
            await message.answer(f"✅ {user_id} admin qilib qo'shildi!")
        else:
            await message.answer(f"❌ {user_id} allaqachon admin!")
    except:
        await message.answer("❌ To'g'ri User ID yuboring!")
        return
    
    await state.clear()

@router.message(F.text == "❌ Admin o'chirish", StateFilter("admin_admins_menu"))
async def admin_remove_admin(message: Message, state: FSMContext):
    text = "❌ Admin o'chirish\n\nAdmin User ID yuboring:"
    await message.answer(text)
    await state.set_state("admin_remove_admin_id")

@router.message(StateFilter("admin_remove_admin_id"))
async def admin_remove_admin_handler(message: Message, state: FSMContext, db: Database):
    try:
        user_id = int(message.text.strip())
        if db.remove_admin(user_id):
            await message.answer(f"✅ {user_id} adminlikdan chiqarildi!")
        else:
            await message.answer(f"❌ {user_id} admin emas!")
    except:
        await message.answer("❌ To'g'ri User ID yuboring!")
        return
    
    await state.clear()

@router.message(F.text == "🗄️ Backup", StateFilter("admin_menu"))
async def admin_backup_msg(message: Message, state: FSMContext, db: Database, bot: Bot):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    await state.clear()
    try:
        from aiogram.types import FSInputFile
        file = FSInputFile(db.db_path, filename="backup.db")
        await bot.send_document(message.from_user.id, file, caption="🗄️ Database Backup")
        await message.answer("✅ Backup yuborildi!")
        logger.info(f"Backup created by {message.from_user.id}")
    except Exception as e:
        await message.answer(f"❌ Backup xato: {str(e)}")

async def categories_menu(query: CallbackQuery, state: FSMContext, db: Database):
    await state.clear()
    categories = db.get_all_categories()
    text = "📂 Kategoriyalarni tanlang:"
    await query.message.edit_text(text, reply_markup=kb.categories_kb(categories))

@router.callback_query(F.data.startswith("cat:"))
async def category_selected(query: CallbackQuery, state: FSMContext, db: Database):
    await state.clear()
    category_id = int(query.data.split(":")[1])
    text = f"🎯 Kategoriya tanlandi\n\nNima qilmoqchisiz?"
    await query.message.edit_text(text, reply_markup=kb.category_menu_kb(category_id))

@router.callback_query(F.data.startswith("cat_new:"))
async def category_new_content(query: CallbackQuery, state: FSMContext, db: Database):
    category_id = int(query.data.split(":")[1])
    contents = db.get_latest_content(category_id, limit=50)
    
    if not contents:
        text = "❌ Bu kategoriyada kontent yo'q"
        await query.message.edit_text(text, reply_markup=kb.back_kb("categories"))
        return
    
    await state.set_state("viewing_category_new")
    await state.update_data(contents=contents, category_id=category_id, current_page=1)
    
    page = 1
    page_size = ITEMS_PER_PAGE
    total_pages = ceil(len(contents) / page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = contents[start_idx:end_idx]
    
    text = f"🆕 Yangi qo'shilgan ({len(contents)} ta):"
    await query.message.edit_text(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "cat_new_page", "categories"))

@router.callback_query(F.data.startswith("cat_top:"))
async def category_top_content(query: CallbackQuery, state: FSMContext, db: Database):
    category_id = int(query.data.split(":")[1])
    contents = db.get_top_content(category_id, limit=50)
    
    if not contents:
        text = "❌ Bu kategoriyada kontent yo'q"
        await query.message.edit_text(text, reply_markup=kb.back_kb("categories"))
        return
    
    await state.set_state("viewing_category_top")
    await state.update_data(contents=contents, category_id=category_id, current_page=1)
    
    page = 1
    page_size = ITEMS_PER_PAGE
    total_pages = ceil(len(contents) / page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = contents[start_idx:end_idx]
    
    text = f"🔥 Top ({len(contents)} ta):"
    await query.message.edit_text(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "cat_top_page", "categories"))

# ===== NEW CONTENT =====
@router.callback_query(F.data == "new_content")
async def new_content(query: CallbackQuery, state: FSMContext, db: Database):
    await state.clear()
    contents = db.get_latest_content(limit=50)
    
    if not contents:
        text = "❌ Yangi kontent yo'q"
        await query.message.edit_text(text, reply_markup=kb.back_kb("main_menu"))
        return
    
    await state.set_state("viewing_new")
    await state.update_data(contents=contents, current_page=1)
    
    page = 1
    page_size = ITEMS_PER_PAGE
    total_pages = ceil(len(contents) / page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = contents[start_idx:end_idx]
    
    text = f"🆕 Yangi qo'shilgan ({len(contents)} ta):"
    await query.message.edit_text(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "new_page", "main_menu"))

# ===== TOP CONTENT =====
@router.callback_query(F.data == "top_content")
async def top_content(query: CallbackQuery, state: FSMContext, db: Database):
    await state.clear()
    contents = db.get_top_content(limit=50)
    
    if not contents:
        text = "❌ Top kontent yo'q"
        await query.message.edit_text(text, reply_markup=kb.back_kb("main_menu"))
        return
    
    await state.set_state("viewing_top")
    await state.update_data(contents=contents, current_page=1)
    
    page = 1
    page_size = ITEMS_PER_PAGE
    total_pages = ceil(len(contents) / page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = contents[start_idx:end_idx]
    
    text = f"🔥 Top ({len(contents)} ta):"
    await query.message.edit_text(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "top_page", "main_menu"))

# ===== PROFILE =====
@router.callback_query(F.data == "profile")
async def profile_menu(query: CallbackQuery, state: FSMContext):
    await state.clear()
    text = "👤 Profil\n\nTanlang:"
    await query.message.edit_text(text, reply_markup=kb.profile_kb())

@router.callback_query(F.data == "favorites")
async def favorites_menu(query: CallbackQuery, state: FSMContext, db: Database):
    user_id = query.from_user.id
    contents = db.get_user_favorites(user_id, limit=50)
    
    if not contents:
        text = "⭐ Sevimlilar ro'yxati bo'sh"
        await query.message.edit_text(text, reply_markup=kb.back_kb("profile"))
        return
    
    await state.set_state("viewing_favorites")
    await state.update_data(contents=contents, current_page=1)
    
    page = 1
    page_size = ITEMS_PER_PAGE
    total_pages = ceil(len(contents) / page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = contents[start_idx:end_idx]
    
    text = f"⭐ Sevimlilar ({len(contents)} ta):"
    await query.message.edit_text(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "fav_page", "profile"))

@router.callback_query(F.data == "vip_status")
async def vip_status(query: CallbackQuery, db: Database):
    user_id = query.from_user.id
    is_vip = db.is_vip(user_id)
    
    if is_vip:
        vip_until = db.get_vip_until(user_id)
        text = f"💎 VIP Status: FAOL ✅\n\nTugab turadi: {vip_until}"
    else:
        text = "💎 VIP Status: FAOL EMAS ❌\n\nVIP foydalanuvchi emas"
    
    await query.message.edit_text(text, reply_markup=kb.back_kb("profile"))

# ===== VIP MENU =====
@router.callback_query(F.data == "vip_menu")
async def vip_menu(query: CallbackQuery, state: FSMContext):
    await state.clear()
    text = "💎 VIP Obuna\n\n🎁 Afzalliklar:\n✅ Reklamalar chiqmaydi\n✅ VIP kontent\n✅ Ko'p so'rov qilish\n\nTo'lov usuli tanlang:"
    await query.message.edit_text(text, reply_markup=kb.vip_menu_kb())

# ===== CONTENT VIEWING =====
@router.callback_query(F.data.startswith("view:"))
async def view_content(query: CallbackQuery, state: FSMContext, db: Database):
    code = query.data.split(":")[1]
    user_id = query.from_user.id
    
    content = db.get_content_by_code(code)
    if not content:
        await query.answer("❌ Kontent topilmadi", show_alert=True)
        return
    
    # Check VIP only
    if content['is_vip_only'] and not db.is_vip(user_id):
        text = f"🎬 {content['title']} (K{content['code']})\n\n💎 VIP: Ha\n\n⚠️ Bu kontent faqat VIP foydalanuvchilar uchun mavjud"
        await query.message.edit_text(text, reply_markup=kb.back_kb("main_menu"))
        return
    
    # Store selected content in state
    await state.update_data(selected_content_code=code)
    
    text = f"🎬 {content['title']} (K{content['code']})\n"
    if content['year']:
        text += f"🗓 Yil: {content['year']}\n"
    text += f"📝 {content['description']}\n"
    
    if content['is_vip_only']:
        text += "\n💎 VIP: Ha"
    
    await query.message.edit_text(text, reply_markup=kb.content_detail_kb(code, user_id, db))

# ===== PAYMENT APPROVAL =====
@router.callback_query(F.data.startswith("approve_payment:"))
async def approve_payment_callback(query: CallbackQuery, db: Database, bot: Bot):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return
    
    payment_id = int(query.data.split(":")[1])
    if db.approve_payment(payment_id, query.from_user.id):
        payment = db.get_payment(payment_id)
        user_id = payment['user_id']
        
        # Send notification to user
        await bot.send_message(
            chat_id=user_id,
            text="✅ VIP To'lovingiz Tasdiqlandi!\n\n💎 Siz VIP foydalanuvchi bo'ldingiz! 30 kunlik VIP xususiyatlaridan foydalanishingiz mumkin."
        )
        
        await query.answer("✅ To'lov tasdiqlandi!", show_alert=True)
        await query.message.edit_text("✅ To'lov tasdiqlandi!")
    else:
        await query.answer("❌ Xato yuz berdi!", show_alert=True)

@router.callback_query(F.data.startswith("reject_payment:"))
async def reject_payment_callback(query: CallbackQuery, db: Database, bot: Bot):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return
    
    payment_id = int(query.data.split(":")[1])
    if db.reject_payment(payment_id, query.from_user.id):
        payment = db.get_payment(payment_id)
        user_id = payment['user_id']
        
        # Send notification to user
        await bot.send_message(
            chat_id=user_id,
            text="❌ VIP To'lovingiz Rad Etildi!\n\nAdminni yoki hatolikni tekshiring!"
        )
        
        await query.answer("✅ To'lov rad etildi!", show_alert=True)
        await query.message.edit_text("❌ To'lov rad etildi!")
    else:
        await query.answer("❌ Xato yuz berdi!", show_alert=True)


@router.callback_query(F.data.startswith("confirm_delete:"))
async def confirm_delete_callback(query: CallbackQuery, db: Database, bot: Bot):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return

    try:
        content_id = int(query.data.split(":")[1])
    except Exception:
        await query.answer("❌ Noto'g'ri so'rov", show_alert=True)
        return

    success, channel_msg_id = db.delete_content(content_id)
    if success:
        # determine channel id: prefer DB settings, fallback to env CONTENT_CHANNEL_ID
        settings = db.get_settings() or {}
        chan_id = settings.get('content_channel_id') or CONTENT_CHANNEL_ID
        # try to remove message from channel if present
        if channel_msg_id and chan_id:
            try:
                await bot.delete_message(chat_id=chan_id, message_id=channel_msg_id)
            except Exception:
                pass

        await query.answer("✅ Kino o'chirildi", show_alert=True)
        try:
            await query.message.edit_text("✅ Kino muvaffaqiyatli o'chirildi.")
        except:
            pass
    else:
        await query.answer("❌ O'chirishda xato yuz berdi", show_alert=True)


@router.callback_query(F.data == "cancel_delete")
async def cancel_delete_callback(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return

    await query.answer("❌ O'chirish bekor qilindi", show_alert=True)
    try:
        await query.message.edit_text("❌ O'chirish bekor qilindi")
    except:
        pass


@router.callback_query(F.data.startswith("remove_vip:"))
async def remove_vip_callback(query: CallbackQuery, db: Database, bot: Bot):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return

    try:
        uid = int(query.data.split(":")[1])
    except Exception:
        await query.answer("❌ Noto'g'ri foydalanuvchi", show_alert=True)
        return

    if db.remove_vip(uid):
        await query.answer("✅ VIP holati olib tashlandi", show_alert=True)
        try:
            await query.message.edit_text("✅ VIP o'chirildi")
        except:
            pass
        # notify user optionally
        try:
            await bot.send_message(uid, "⚠️ Sizning VIP obunangiz admin tomonidan bekor qilindi.")
        except:
            pass
    else:
        await query.answer("❌ O'chirishda xato yuz berdi", show_alert=True)

@router.callback_query(F.data.startswith("watch:"))
async def watch_content(query: CallbackQuery, db: Database, bot: Bot):
    code = query.data.split(":")[1]
    user_id = query.from_user.id
    
    content = db.get_content_by_code(code)
    if not content:
        await query.answer("❌ Kontent topilmadi", show_alert=True)
        return
    
    # Check VIP only
    if content['is_vip_only'] and not db.is_vip(user_id):
        await query.answer("💎 Bu kontent VIP uchun. Obunaga o'tish kerak", show_alert=True)
        return
    
    # Increment views
    db.increment_views(content['id'])
    
    # Send video to user
    if content['video_file_id']:
        try:
            caption = f"🎬 {content['title']}\n"
            if content['year']:
                caption += f"📅 Yil: {content['year']}\n"
            if content['description']:
                caption += f"\n{content['description']}\n"
            caption += f"\n🔍 Kod: {content['code']}"
            
            await bot.send_video(
                chat_id=user_id,
                video=content['video_file_id'],
                caption=caption
            )
            await query.answer("✅ Video yuborildi", show_alert=False)
        except Exception as e:
            logger.error(f"Video send error: {e}")
            await query.answer("❌ Video yuborishda xato", show_alert=True)
    else:
        await query.answer("❌ Video topilmadi", show_alert=True)

@router.callback_query(F.data.startswith("favorite:"))
async def toggle_favorite(query: CallbackQuery, db: Database):
    code = query.data.split(":")[1]
    user_id = query.from_user.id
    
    content = db.get_content_by_code(code)
    if not content:
        await query.answer("❌ Kontent topilmadi", show_alert=True)
        return
    
    if db.is_favorite(user_id, content['id']):
        db.remove_favorite(user_id, content['id'])
        await query.answer("⭐ Sevimlilardan o'chirildi", show_alert=True)
    else:
        db.add_favorite(user_id, content['id'])
        await query.answer("⭐ Sevimlilarga qo'shildi", show_alert=True)

# ===== AD CLICK TRACKING =====
@router.callback_query(F.data.startswith("ad_click:"))
async def ad_click_callback(query: CallbackQuery, db: Database):
    ad_id = int(query.data.split(":")[1])
    user_id = query.from_user.id
    
    # Log the click
    db.log_ad_click(ad_id, user_id)
    
    # Answer callback
    await query.answer("🔗 Havola ochilyapti...", show_alert=False)

# ===== CHANNEL CALLBACKS (open:CODE) =====
@router.callback_query(F.data.startswith("open:"))
async def channel_open(query: CallbackQuery, state: FSMContext, db: Database, bot):
    code = query.data.split(":")[1]
    user_id = query.from_user.id
    
    db.register_user(user_id)
    
    if check_banned(user_id, db):
        await query.answer("❌ Siz ban qilingansiz!", show_alert=True)
        return
    
    # Check force subscribe
    if not await check_force_subscribe(user_id, bot, db):
        channels = db.get_force_channels()
        text = "📢 Avval kanallarga obuna bo'ling:\n\n"
        await query.message.edit_text(text, reply_markup=kb.force_subscribe_kb(channels))
        await query.answer("📢 Kanallarni tekshiring")
        return
    
    await state.clear()
    await state.update_data(selected_content_code=code)
    
    content = db.get_content_by_code(code)
    if not content:
        await query.answer("❌ Kontent topilmadi", show_error=True)
        return
    
    text = f"🎬 {content['title']} (K{content['code']})\n"
    if content['year']:
        text += f"🗓 Yil: {content['year']}\n"
    text += f"📝 {content['description']}\n"
    
    if content['is_vip_only']:
        text += "\n💎 VIP: Ha"
    
    await query.message.edit_text(text, reply_markup=kb.content_detail_kb(code, user_id, db))

# ===== PAGINATION =====
@router.callback_query(F.data.startswith("new_page:"))
async def new_page_pagination(query: CallbackQuery, state: FSMContext, db: Database):
    page = int(query.data.split(":")[1])
    data = await state.get_data()
    contents = data.get('contents', [])
    
    page_size = ITEMS_PER_PAGE
    total_pages = ceil(len(contents) / page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = contents[start_idx:end_idx]
    
    text = f"🆕 Yangi qo'shilgan ({len(contents)} ta):"
    await query.message.edit_text(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "new_page", "main_menu"))

@router.callback_query(F.data.startswith("top_page:"))
async def top_page_pagination(query: CallbackQuery, state: FSMContext, db: Database):
    page = int(query.data.split(":")[1])
    data = await state.get_data()
    contents = data.get('contents', [])
    
    page_size = ITEMS_PER_PAGE
    total_pages = ceil(len(contents) / page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = contents[start_idx:end_idx]
    
    text = f"🔥 Top ({len(contents)} ta):"
    await query.message.edit_text(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "top_page", "main_menu"))

@router.callback_query(F.data.startswith("search_page:"))
async def search_page_pagination(query: CallbackQuery, state: FSMContext):
    page = int(query.data.split(":")[1])
    data = await state.get_data()
    results = data.get('search_results', [])
    
    page_size = ITEMS_PER_PAGE
    total_pages = ceil(len(results) / page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = results[start_idx:end_idx]
    
    text = f"🔎 Topilgan: {len(results)} ta\n\n"
    for content in page_results:
        status = " 💎" if content.get('is_vip_only') else ""
        text += f"🎬 {content['title']}{status}\n"
    
    await query.message.edit_text(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "search_page", "main_menu"))

@router.callback_query(F.data.startswith("fav_page:"))
async def fav_page_pagination(query: CallbackQuery, state: FSMContext):
    page = int(query.data.split(":")[1])
    data = await state.get_data()
    contents = data.get('contents', [])
    
    page_size = ITEMS_PER_PAGE
    total_pages = ceil(len(contents) / page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_results = contents[start_idx:end_idx]
    
    text = f"⭐ Sevimlilar ({len(contents)} ta):"
    await query.message.edit_text(text, reply_markup=kb.content_list_kb(page_results, page, total_pages, "fav_page", "profile"))

# ===== FORCE SUBSCRIBE =====
@router.callback_query(F.data == "check_subscribe")
async def check_subscribe(query: CallbackQuery, state: FSMContext, db: Database, bot):
    user_id = query.from_user.id
    db.register_user(user_id)
    
    if not await check_force_subscribe(user_id, bot, db):
        await query.answer("📢 Barcha kanallarga obuna bo'ling!", show_alert=True)
        return
    
    await state.clear()
    text = "✅ Tekshiruv muvaffaqiyatli!\n\nAsosiy menyuga o'tish..."
    # edit current message first (no keyboard)
    await query.message.edit_text(text)
    # send a separate message with main menu (reply keyboard for user)
    await query.message.answer("🎬 Kino Hubga xush kelibsiz!", reply_markup=kb.main_menu_kb(user_id, db))
    await query.answer("✅ Muvaffaqiyatli!")

# ===== ADMIN HANDLERS =====
@router.callback_query(F.data == "admin")
async def admin_menu_callback(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Siz admin emassiz!", show_alert=True)
        return
    
    text = "👨‍💻 Admin Paneli\n\nTanlang:"
    await query.message.edit_text(text, reply_markup=kb.admin_menu_kb())

@router.callback_query(F.data == "noop")
async def noop_callback(query: CallbackQuery):
    await query.answer()

@router.callback_query(F.data == "admin_add_content")
async def admin_add_content(query: CallbackQuery, state: FSMContext, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Siz admin emassiz!", show_alert=True)
        return
    
    categories = db.get_all_categories()
    if not categories:
        await query.answer("❌ Kategoriya yo'q! Avval kategoriya qo'shing.", show_alert=True)
        return
    
    buttons = []
    for cat in categories:
        buttons.append([InlineKeyboardButton(text=cat['name'], callback_data=f"admin_add_cat_{cat['id']}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")])
    
    text = "➕ Kontent qo'shish\n\nKategoriya tanlang:"
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("admin_add_cat_"))
async def admin_add_cat_select(query: CallbackQuery, state: FSMContext):
    cat_id = int(query.data.split("_")[-1])
    await state.set_state(AdminStates.content_code)
    await state.update_data(cat_id=cat_id)
    await query.message.edit_text("📝 Kontentin kodini yozing (maslan: FILM001):", reply_markup=kb.back_kb("admin_add_content"))

@router.message(AdminStates.content_code)
async def admin_content_code_handler(message: Message, state: FSMContext):
    await state.update_data(code=message.text)
    await state.set_state(AdminStates.content_title)
    await message.answer("📝 Nomi:")

@router.message(AdminStates.content_title)
async def admin_content_title_handler(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(AdminStates.content_year)
    await message.answer("📅 Yili:")

@router.message(AdminStates.content_year)
async def admin_content_year_handler(message: Message, state: FSMContext):
    try:
        int(message.text)
    except:
        await message.answer("❌ Yil son bo'lishi kerak!")
        return
    
    await state.update_data(year=message.text)
    await state.set_state(AdminStates.content_image)
    await message.answer("🖼️ Suratni yuboring:")

@router.message(AdminStates.content_image)
async def admin_content_image_handler(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer("❌ Surat yuboring!")
        return
    
    await state.update_data(image=message.photo[-1].file_id)
    await state.set_state(AdminStates.content_description)
    await message.answer("📝 Ta'rif:")

@router.message(AdminStates.content_description)
async def admin_content_description_handler(message: Message, state: FSMContext, db: Database):
    await state.update_data(description=message.text)
    data = await state.get_data()
    await state.clear()
    
    db.add_content(
        code=data['code'],
        title=data['title'],
        category_id=data['cat_id'],
        year=data['year'],
        image=data['image'],
        description=data['description']
    )
    
    text = f"✅ Kontent qo'shildi!\n\nKod: {data['code']}\nNomi: {data['title']}"
    await message.answer(text, reply_markup=kb.admin_menu_kb())

# ===== ADMIN CONTENT MANAGEMENT =====
@router.message(F.text == "➕ Kontent qo'shish")
async def add_content_category_msg(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    categories = db.get_all_categories()
    buttons = []
    for cat in categories:
        buttons.append([KeyboardButton(text=cat['name'])])
    buttons.append([KeyboardButton(text="⬅️ Orqaga")])
    
    text = "➕ Kontent qo'shish\n\nKategoriya tanlang:"
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))
    await state.set_state(ContentStates.cat)

@router.message(ContentStates.cat)
async def add_content_name(message: Message, state: FSMContext, db: Database):
    text = message.text
    if text == "⬅️ Orqaga":
        await state.clear()
        await message.answer("🔙 Orqaga", reply_markup=main_menu_kb(message.from_user.id, db))
        return
    
    # Get category by name
    cat = None
    for c in db.get_all_categories():
        if c['name'] == text:
            cat = c
            break
    
    if not cat:
        await message.answer("❌ Bunday kategoriya yo'q. Qaytadan tanlang.")
        return
    
    await state.update_data(content_category_id=cat['id'])
    await state.set_state(ContentStates.name)
    await message.answer("📝 Kontent nomini yuboring:")

@router.message(ContentStates.name)
async def content_year_input(message: Message, state: FSMContext):
    await state.update_data(content_name=message.text)
    await state.set_state(ContentStates.year)
    text = "📅 Yili (ixtiyoriy, yo'q bo'lsa -1 yuboring):"
    await message.answer(text)

@router.message(ContentStates.year)
async def content_desc_input(message: Message, state: FSMContext):
    try:
        year = int(message.text) if message.text != "-1" else None
        await state.update_data(content_year=year)
    except:
        await message.answer("❌ Raqam yuboring!")
        return
    
    await state.set_state(ContentStates.desc)
    template = """📝 Tavsif yuboring (ixtiyoriy):

Namuna:
🎬 Janr: Drama
⭐ Reyting: 8.5/10
🎭 Aktyor: Ismlar...
📊 Davomiyligi: 2 soat
🌍 Mamlakat: USA

Shunga o'xshash yozishingiz mumkin yoki o'zingiz yozing.
/skip bo'lsa tavsif bo'lmaydi"""
    await message.answer(template)

@router.message(ContentStates.desc)
async def content_poster_input(message: Message, state: FSMContext):
    if message.text.lower() == "/skip":
        await state.update_data(content_desc="")
    else:
        await state.update_data(content_desc=message.text)
    
    await state.set_state(ContentStates.poster)
    text = "📸 Rasmi yuboring (ixtiyoriy, /skip bo'lsa):"
    await message.answer(text)

@router.message(ContentStates.poster)
async def content_vip_type_select(message: Message, state: FSMContext):
    if message.photo:
        await state.update_data(content_poster_file_id=message.photo[-1].file_id)
    elif message.text.lower() == "/skip":
        await state.update_data(content_poster_file_id=None)
    else:
        await message.answer("❌ Rasm yuboring yoki /skip yozing!")
        return
    
    await state.set_state(ContentStates.vip)
    text = "💎 Kino turi tanlang:\n\n🟢 ODDIY - Barcha foydalanuvchilar ko'rishi mumkin\n🔴 VIP - Faqat VIP obuna bo'lganlar ko'rishi mumkin"
    buttons = [
        [KeyboardButton(text="🟢 Oddiy"), KeyboardButton(text="🔴 VIP")]
    ]
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))

@router.message(ContentStates.vip)
async def content_vip_type_handler(message: Message, state: FSMContext):
    if message.text == "🟢 Oddiy":
        await state.update_data(content_is_vip=False)
    elif message.text == "🔴 VIP":
        await state.update_data(content_is_vip=True)
    else:
        await message.answer("❌ Iltimos tugmalardan birini tanlang!")
        return
    
    await state.set_state(ContentStates.video)
    text = "🎬 Videoni yuboring:"
    await message.answer(text)

@router.message(ContentStates.video)
async def content_finish(message: Message, state: FSMContext, db: Database, bot: Bot):
    if not message.video:
        await message.answer("❌ Iltimos videoni yuboring!")
        return
    
    data = await state.get_data()
    video_file_id = message.video.file_id
    is_vip = data.get("content_is_vip", False)
    
    # Save to database (code avtomatik yaratiladi)
    success, code = db.add_content(
        title=data.get("content_name"),
        year=data.get("content_year"),
        description=data.get("content_desc", ""),
        category_id=data.get("content_category_id"),
        poster_file_id=data.get("content_poster_file_id"),
        video_file_id=video_file_id,
        is_vip_only=is_vip
    )
    
    if not success or not code:
        await message.answer("❌ Kontent saqlashda xato!")
        return
    
    # Prepare caption for channel
    caption = f"🎬 {data.get('content_name')}\n"
    if data.get('content_year'):
        caption += f"📅 Yil: {data.get('content_year')}\n"
    caption += f"\n🔍 Kod: <code>{code}</code>"
    
    if data.get('content_desc'):
        caption += f"\n\n{data.get('content_desc')}"
    
    if is_vip:
        caption += "\n\n🔴 VIP CONTENT"
    
    try:
        # Send ONLY photo/poster to channel with watch button
        if data.get('content_poster_file_id'):
            msg = await bot.send_photo(
                chat_id=CONTENT_CHANNEL_ID,
                photo=data.get('content_poster_file_id'),
                caption=caption,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎬 Ko'rish", callback_data=f"watch:{code}")]
                ])
            )
        else:
            msg = await bot.send_message(
                chat_id=CONTENT_CHANNEL_ID,
                text=caption,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎬 Ko'rish", callback_data=f"watch:{code}")]
                ])
            )
        
        logger.info(f"Content posted to channel: {code}")
    except Exception as e:
        logger.error(f"Channel post error: {e}")
        await message.answer(f"⚠️ Kanalga yuborishda xato: {e}")
    
    await state.clear()
    user_id = message.from_user.id
    await message.answer(f"✅ Kontent qo'shildi!\n🔍 Kod: {code}", reply_markup=main_menu_kb(user_id, db))

async def save_content(query: CallbackQuery, state: FSMContext, db: Database, bot):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return
    
    data = await state.get_data()
    
    success, code = db.add_content(
        title=data['content_name'],
        category_id=data['content_category_id'],
        year=data.get('content_year'),
        description=data.get('content_desc'),
        poster_file_id=data.get('content_poster_file_id'),
        video_file_id=data.get('content_video_file_id'),
        video_url=data.get('content_video_url'),
        is_vip_only=data.get('content_vip_only', False)
    )
    
    if not success:
        await query.answer("❌ Xato!", show_alert=True)
        return
    
    # Post to channel
    settings = db.get_settings()
    channel_id = settings.get('content_channel_id')
    
    if channel_id:
        try:
            caption = f"🎬 {data['content_name']}\n"
            caption += f"🆔 Kod: {code}\n"
            if data.get('content_year'):
                caption += f"🗓 Yil: {data['content_year']}\n"
            caption += f"📝 {data.get('content_desc', '')}\n"
            if data.get('content_vip_only'):
                caption += "\n💎 VIP: Ha"
            
            msg = await bot.send_photo(
                channel_id,
                photo=data['content_poster_file_id'],
                caption=caption,
                reply_markup=kb.channel_post_kb(code)
            )
            
            db.publish_content(code, msg.message_id)
        except Exception as e:
            pass
    
    await state.clear()
    text = f"✅ Kontent qo'shildi!\n\nKod: {code}"
    await query.message.edit_text(text, reply_markup=kb.admin_menu_kb())

# ===== CATEGORIES MANAGEMENT =====
@router.callback_query(F.data == "admin_categories")
async def admin_categories(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return
    
    text = "🗂 Kategoriyalar boshqaruvi"
    buttons = [
        [InlineKeyboardButton(text="➕ Qo'shish", callback_data="admin_cat_add")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")]
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data == "admin_cat_add")
async def admin_cat_add(query: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.category)
    text = "➕ Kategoriya nomini yuboring:"
    await query.message.edit_text(text, reply_markup=kb.back_kb("admin_categories"))

@router.message(AdminStates.category)
async def save_category(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    success = db.add_category(message.text)
    await state.clear()
    
    if success:
        text = f"✅ '{message.text}' kategoriya qo'shildi!"
    else:
        text = "❌ Xato yoki duplikat"
    
    buttons = [
        [InlineKeyboardButton(text="➕ Qo'shish", callback_data="admin_cat_add")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_categories")]
    ]
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

# ===== ADS MANAGEMENT =====
@router.callback_query(F.data == "admin_ads")
async def admin_ads_menu(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return
    
    text = "💰 Reklama boshqaruvi"
    buttons = [
        [InlineKeyboardButton(text="➕ Reklama qo'shish", callback_data="admin_ad_add")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")]
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data == "admin_ad_add")
async def admin_ad_add(query: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.ad_title)
    text = "➕ Reklama sarlavhasi yuboring:"
    await query.message.edit_text(text, reply_markup=kb.back_kb("admin_ads"))

@router.message(AdminStates.ad_title)
async def ad_type(message: Message, state: FSMContext):
    await state.update_data(ad_title=message.text)
    await state.set_state(AdminStates.ad_button)
    
    text = "📌 Tugma matni yuboring:"
    await message.answer(text)

@router.message(AdminStates.ad_button)
async def ad_button_url(message: Message, state: FSMContext):
    await state.update_data(ad_button_text=message.text)
    await state.set_state(AdminStates.ad_url)
    
    text = "🔗 Tugma URL yuboring:"
    await message.answer(text)

@router.message(AdminStates.ad_url)
async def save_ad(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    data = await state.get_data()
    success = db.add_ad(
        title=data['ad_title'],
        button_text=data['ad_button_text'],
        button_url=message.text
    )
    
    await state.clear()
    
    if success:
        text = "✅ Reklama qo'shildi!"
    else:
        text = "❌ Xato!"
    
    buttons = [
        [InlineKeyboardButton(text="➕ Reklama qo'shish", callback_data="admin_ad_add")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_ads")]
    ]
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

# ===== FORCE CHANNELS =====
@router.callback_query(F.data == "admin_force_channels")
async def admin_force_channels(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return
    
    channels = db.get_force_channels()
    text = f"✅ Majburiy obuna kanalları ({len(channels)} ta)\n\n"
    for ch in channels:
        text += f"📢 {ch['chat_id_or_username']}\n"
    
    buttons = [
        [InlineKeyboardButton(text="➕ Qo'shish", callback_data="add_force_ch")],
        [InlineKeyboardButton(text="❌ O'chirish", callback_data="admin_remove_force")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")]
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data == "add_force_ch")
async def add_force_ch(query: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.force_ch)
    text = "📢 Kanal username yoki ID yuboring:"
    await query.message.edit_text(text, reply_markup=kb.back_kb("admin_force_channels"))


@router.callback_query(F.data == "admin_remove_force")
async def admin_remove_force(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return

    channels = db.get_force_channels()
    if not channels:
        await query.answer("ℹ️ Majburiy kanal yo'q", show_alert=True)
        return

    buttons = []
    for ch in channels:
        buttons.append([InlineKeyboardButton(text=ch['chat_id_or_username'], callback_data=f"remove_force_ch:{ch['id']}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_force_channels")])

    await query.message.edit_text("❌ O'chirish uchun kanalni tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("remove_force_ch:"))
async def remove_force_ch_callback(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return

    ch_id = int(query.data.split(":")[1])
    if db.remove_force_channel(ch_id):
        await query.answer("✅ Kanal o'chirildi", show_alert=True)
    else:
        await query.answer("❌ O'chirishda xato", show_alert=True)

    # refresh list
    await admin_force_channels(query, db)

@router.message(AdminStates.force_ch)
async def save_force_channel(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    await state.set_state(AdminStates.force_link)
    await state.update_data(force_channel=message.text)
    
    text = "🔗 Invite link yuboring (ixtiyoriy):"
    await message.answer(text)

@router.message(AdminStates.force_link)
async def save_force_channel_link(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    data = await state.get_data()
    db.add_force_channel(data['force_channel'], message.text if message.text != "-" else None)
    
    await state.clear()
    text = "✅ Kanal qo'shildi!"
    await message.answer(text, reply_markup=kb.back_kb("admin"))

# ===== VIP SETTINGS =====
@router.callback_query(F.data == "admin_vip_settings")
async def admin_vip_settings(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return
    
    settings = db.get_vip_settings()
    text = (
        f"💎 VIP Sozlamalari\n\n"
        f"💵 So'm: {settings.get('uzs_price',30000):,}\n"
        f"⭐ Stars: {settings.get('stars_price',300)}\n\n"
        "Qaysi narsani o'zgartirmoqchisiz?"
    )
    buttons = [
        [InlineKeyboardButton(text="💵 So'm narxini o'zgartirish", callback_data="admin_change_uzs_price")],
        [InlineKeyboardButton(text="⭐ Stars narxini o'zgartirish", callback_data="admin_change_stars_price")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")],
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data == "admin_change_uzs_price")
async def admin_change_uzs_price_cb(query: CallbackQuery, state: FSMContext):
    await state.set_state("admin_uzs_price")
    await query.message.edit_text("💵 Yangi so'm narxini yuboring (raqam):", reply_markup=kb.back_kb("admin_vip_settings"))

@router.callback_query(F.data == "admin_change_stars_price")
async def admin_change_stars_price_cb(query: CallbackQuery, state: FSMContext):
    await state.set_state("admin_stars_price")
    await query.message.edit_text("⭐ Yangi Stars narxini yuboring (raqam):", reply_markup=kb.back_kb("admin_vip_settings"))

# reuse existing message handlers for admin_uzs_price and admin_stars_price defined earlier

# ===== ANALYTICS =====
@router.callback_query(F.data == "admin_analytics")
async def admin_analytics(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return
    
    top_searches = db.get_top_searches(5)
    not_found = db.get_not_found_searches(5)
    
    text = "📈 Analytics\n\n"
    text += "🔎 Top qidiruvlar:\n"
    for search in top_searches:
        text += f"• {search['query_text']} ({search['count']} ta)\n"
    
    text += "\n❌ Topilmagan:\n"
    for search in not_found:
        text += f"• {search['query_text']} ({search['count']} ta)\n"
    
    ad_stats = db.get_ad_stats()
    text += "\n💰 Reklama Statistikasi:\n"
    for stat in ad_stats[:5]:
        impressions = stat['impressions'] or 0
        clicks = stat['clicks'] or 0
        ctr = (clicks / impressions * 100) if impressions > 0 else 0
        text += f"• Ad {stat['ad_id']}: {impressions} imp, {clicks} click, {ctr:.1f}% CTR\n"
    
    await query.message.edit_text(text, reply_markup=kb.back_kb("admin"))

# ===== ADMIN MANAGEMENT =====
@router.callback_query(F.data == "admin_admins")
async def admin_admins_menu(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return
    
    admins = db.get_all_admins()
    text = f"👮 Adminlar ({len(admins)} ta)\n\n"
    for admin_id in admins:
        text += f"• {admin_id}\n"
    
    buttons = [
        [InlineKeyboardButton(text="➕ Qo'shish", callback_data="add_admin_user")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")]
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data == "add_admin_user")
async def add_admin_user(query: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.admin_id)
    text = "👮 User ID yuboring:"
    await query.message.edit_text(text, reply_markup=kb.back_kb("admin_admins"))

@router.message(AdminStates.admin_id)
async def save_admin(message: Message, state: FSMContext, db: Database):
    if not check_admin(message.from_user.id, db):
        await message.answer("❌ Admin emas!")
        return
    
    try:
        user_id = int(message.text)
        db.add_admin(user_id, message.from_user.id)
        await state.clear()
        text = f"✅ {user_id} admin qilindu!"
    except:
        text = "❌ Xato! Raqam yuboring"
    
    await message.answer(text, reply_markup=kb.back_kb("admin"))

# ===== DB BACKUP =====
@router.callback_query(F.data == "admin_backup")
async def admin_backup(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return
    
    try:
        with open(db.db_path, 'rb') as f:
            await query.bot.send_document(query.from_user.id, f, caption="🗄️ Database Backup")
        text = "✅ Backup yuborildi"
        await query.answer(text, show_alert=True)
    except:
        text = "❌ Xato"
        await query.answer(text, show_alert=True)

# ===== EDIT/HIDE CONTENT =====
@router.callback_query(F.data == "admin_edit_content")
async def admin_edit_content(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return
    
    contents = db.get_all_content(include_hidden=True)
    text = "✏️ Kontentni tanlang:"
    
    buttons = []
    for content in contents[:10]:
        status = " ✅" if content['is_published'] else " ❌"
        buttons.append([InlineKeyboardButton(text=f"{content['title']}{status}", callback_data=f"edit_cont:{content['id']}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin")])
    
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("edit_cont:"))
async def edit_content_details(query: CallbackQuery, db: Database):
    content_id = int(query.data.split(":")[1])
    content = db.get_content_by_id(content_id)
    
    text = f"🎬 {content['title']} (K{content['code']})\n"
    text += f"Status: {'✅ Faol' if content['is_published'] else '❌ Yashirilgan'}\n"
    
    buttons = []
    if content['is_published']:
        buttons.append([InlineKeyboardButton(text="👁 Yashirish", callback_data=f"hide_cont:{content_id}")])
    else:
        buttons.append([InlineKeyboardButton(text="👁 Ko'rsatish", callback_data=f"show_cont:{content_id}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_edit_content")])
    
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("hide_cont:"))
async def hide_content(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return
    
    content_id = int(query.data.split(":")[1])
    db.hide_content(content_id)
    await query.answer("✅ Yashirildi", show_alert=True)
    await query.message.edit_text("✅ Kontent yashirildi!", reply_markup=kb.back_kb("admin_edit_content"))

@router.callback_query(F.data.startswith("show_cont:"))
async def show_content(query: CallbackQuery, db: Database):
    if not check_admin(query.from_user.id, db):
        await query.answer("❌ Admin emas!", show_alert=True)
        return
    
    content_id = int(query.data.split(":")[1])
    db.show_content(content_id)
    await query.answer("✅ Ko'rsatildi", show_alert=True)
    await query.message.edit_text("✅ Kontent ko'rsatildi!", reply_markup=kb.back_kb("admin_edit_content"))

# ===== BOT INITIALIZATION =====

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
db = Database(DB_PATH)
dp = Dispatcher()
dp.include_router(router)

# Middleware to inject db into handlers
class DbMiddleware:
    async def __call__(self, handler, event, data):
        data["db"] = db
        return await handler(event, data)

router.message.middleware(DbMiddleware())
router.callback_query.middleware(DbMiddleware())

async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="🎬 Asosiy menyu"),
    ]
    await bot.set_my_commands(commands)

async def main():
    logger.info("🤖 Bot ishga tushdi...")
    await set_bot_commands()
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN o'rnatilmagan!")
        exit(1)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot to'xtatildi")
