from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, g, has_request_context, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from supabase import create_client, Client
from functools import wraps
from threading import Lock
from typing import Optional
import re
import difflib
from datetime import datetime, timedelta, timezone
import io
import google.generativeai as genai
import os
import json
import random
from dotenv import load_dotenv
# Language detection for multilingual support
try:
    from langdetect import detect
    from langdetect.lang_detect_exception import LangDetectException
    LANGDETECT_AVAILABLE = True
    print("✅ Language detection (langdetect) available")
except ImportError as e:
    LANGDETECT_AVAILABLE = False
    print(f"⚠️ langdetect not available: {e}")
    print("Using fallback language detection based on word patterns")

# 🔧 NEW: Import for PDF generation
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus.flowables import HRFlowable
from reportlab.pdfgen import canvas



# Load environment variables
load_dotenv()

# Captcha Functions
def generate_captcha():
    """Generate a simple math captcha"""
    num1 = random.randint(1, 20)
    num2 = random.randint(1, 20)
    operation = random.choice(['+', '-', '*'])
    
    if operation == '+':
        answer = num1 + num2
        question = f"{num1} + {num2}"
    elif operation == '-':
        # Ensure positive result
        if num1 < num2:
            num1, num2 = num2, num1
        answer = num1 - num2
        question = f"{num1} - {num2}"
    else:  # multiplication
        # Use smaller numbers for multiplication
        num1 = random.randint(2, 10)
        num2 = random.randint(2, 10)
        answer = num1 * num2
        question = f"{num1} × {num2}"
    
    return question, answer

def verify_captcha(user_answer, correct_answer):
    """Verify captcha answer"""
    try:
        return int(user_answer) == int(correct_answer)
    except (ValueError, TypeError):
        return False

# Flask application setup
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", 'your-super-secret-key-change-this-in-production')

# Configure session settings
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Gemini / Google Generative AI configuration (lazy-loaded)
_gemini_model = None
_gemini_model_error = None
_gemini_lock = Lock()


def get_gemini_model():
    """Initialize the Gemini model on first use to avoid slowing down startup."""
    global _gemini_model, _gemini_model_error

    if _gemini_model or _gemini_model_error:
        return _gemini_model

    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        _gemini_model_error = "Missing GEMINI_API_KEY"
        print("⚠️ GEMINI_API_KEY not found; using fallback responses")
        return None

    with _gemini_lock:
        # Double-check inside lock to avoid duplicate initialization
        if _gemini_model or _gemini_model_error:
            return _gemini_model

        try:
            genai.configure(api_key=gemini_key)
            _gemini_model = genai.GenerativeModel('gemini-1.5-flash')
            print("✅ Gemini 1.5 Flash configured (lazy)")
        except Exception as model_error:
            print(f"⚠️ Gemini 1.5 Flash initialization failed: {model_error}")
            try:
                _gemini_model = genai.GenerativeModel('gemini-pro')
                print("✅ Gemini Pro configured (lazy fallback)")
            except Exception as fallback_error:
                _gemini_model_error = f"Gemini init failed: {fallback_error}"
                _gemini_model = None
                print(f"❌ Both Gemini models failed: {fallback_error}")
                print("🔄 Using fallback recommendations")

    return _gemini_model

# Configure Supabase
try:
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        raise Exception("Missing SUPABASE_URL or SUPABASE_KEY in environment")

    supabase: Client = create_client(supabase_url, supabase_key)
    print("✅ Connected to Supabase successfully!")

    try:
        supabase.table('users').select('id').limit(1).execute()
        print("✅ Database tables verified and accessible!")
    except Exception:
        print("⚠️ Database test query failed, but connection established")

except Exception as e:
    print(f"❌ Supabase connection error: {e}")
    supabase = None

# Configure upload settings for Vercel (use /tmp for serverless)
UPLOAD_FOLDER = '/tmp/uploads' if os.environ.get('VERCEL') else 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'txt'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# ==================== HELPER FUNCTIONS ====================

def detect_user_language(text):
    """Detect language of user input with robust fallback"""
    if not text or not text.strip():
        return "English"
    
    # Enhanced word pattern detection (primary method now)
    text_lower = text.lower()
    
    # Extended word lists for better detection
    hindi_words = ['कैसे', 'क्या', 'हाँ', 'नहीं', 'धन्यवाद', 'कहाँ', 'कब', 'कौन', 'कितना', 'मुझे', 'आप', 'हम', 'वह', 'मैं', 'तुम', 'यह', 'है', 'का', 'की', 'के', 'में', 'से', 'पर', 'को', 'भी', 'और', 'सब', 'कुछ', 'बहुत', 'अच्छा', 'बुरा', 'खाना', 'पानी', 'घर', 'काम', 'समय', 'दिन', 'रात', 'सुबह', 'शाम', 'पढ़ाई', 'स्कूल', 'कॉलेज', 'मित्र', 'दोस्त', 'परिवार', 'माता', 'पिता', 'भाई', 'बहन']
    
    marathi_words = ['कसे', 'काय', 'होय', 'नाही', 'धन्यवाद', 'कुठे', 'केव्हा', 'कोण', 'किती', 'मला', 'तुम्ही', 'आम्ही', 'तो', 'ती', 'हे', 'आहे', 'चा', 'ची', 'चे', 'मध्ये', 'पासून', 'वर', 'ला', 'सुद्धा', 'आणि', 'सर्व', 'काही', 'खूप', 'चांगले', 'वाईट', 'जेवण', 'पाणी', 'घर', 'काम', 'वेळ', 'दिवस', 'रात्र', 'सकाळ', 'संध्याकाळ', 'अभ्यास', 'शाळा', 'महाविद्यालय', 'मित्र', 'कुटुंब', 'आई', 'बाबा', 'भाऊ', 'बहीण']
    
    # Count matching words
    hindi_count = sum(1 for word in hindi_words if word in text)
    marathi_count = sum(1 for word in marathi_words if word in text)
    
    # If significant matches found, return that language
    if hindi_count > marathi_count and hindi_count > 0:
        return 'Hindi'
    elif marathi_count > 0:
        return 'Marathi'
    
    # Try langdetect if available and no clear pattern match
    if LANGDETECT_AVAILABLE:
        try:
            detected = detect(text.strip())
            language_map = {
                'hi': 'Hindi',
                'mr': 'Marathi', 
                'en': 'English',
                'ur': 'Hindi',  # Fallback Urdu to Hindi
                'ne': 'Hindi',  # Fallback Nepali to Hindi
            }
            return language_map.get(detected, 'English')
        except Exception:
            pass
    
    # Default to English
    return 'English'

# Create upload directories
try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(os.path.join(UPLOAD_FOLDER, 'certificates'), exist_ok=True)
    os.makedirs(os.path.join(UPLOAD_FOLDER, 'additional'), exist_ok=True)
except Exception as e:
    print(f"Upload folder creation warning: {e}")

# ---------------------------
# 🌐 Multilingual support
# ---------------------------

DEFAULT_LANGUAGE = 'en'
SUPPORTED_LANGUAGES = {
    'en': {'label': 'English'},
    'hi': {'label': 'हिंदी'},
    'mr': {'label': 'मराठी'}
}

TRANSLATIONS_PATH = os.path.join(app.root_path, 'static', 'translations.json')


def load_translations():
    """Load translations from the static JSON file."""
    try:
        with open(TRANSLATIONS_PATH, 'r', encoding='utf-8') as fp:
            data = json.load(fp)
            if isinstance(data, dict):
                print(f"✅ Loaded translations for languages: {list(data.keys())}")
                return data
            print("⚠️ Unexpected translations format. Expected an object keyed by language.")
    except FileNotFoundError:
        print(f"⚠️ translations.json not found at {TRANSLATIONS_PATH}")
    except json.JSONDecodeError as err:
        print(f"⚠️ Error parsing translations.json: {err}")
    return {}


TRANSLATIONS = load_translations()


def _resolve_translation_value(key: str, language: str):
    """Resolve nested translation keys like `nav.home` safely."""
    data = TRANSLATIONS.get(language, {})
    for part in key.split('.'):
        if isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data if isinstance(data, (str, int, float)) else None


def get_translation(key: str, language: Optional[str] = None):
    """Return the translation for the given key and language with fallbacks."""
    if not key:
        return ''

    if language is None:
        if has_request_context():
            language = session.get('language', DEFAULT_LANGUAGE)
        else:
            language = DEFAULT_LANGUAGE

    language = language.lower()
    if language not in SUPPORTED_LANGUAGES:
        language = DEFAULT_LANGUAGE

    value = _resolve_translation_value(key, language)
    if value is None and language != DEFAULT_LANGUAGE:
        value = _resolve_translation_value(key, DEFAULT_LANGUAGE)

    return value if value is not None else key

# PM Internship Scheme Knowledge Base
INTERNSHIP_CONTEXT = """
You are PRIA (PM Internship AI Assistant), an intelligent and helpful AI assistant for the PM Internship Scheme - a prestigious Government of India initiative launched to provide quality internship opportunities to young Indians.

🎯 YOUR PERSONALITY:
- Professional yet friendly and approachable
- Knowledgeable about all aspects of the PM Internship Scheme
- Patient and understanding with user queries
- Proactive in providing relevant information
- Encouraging and supportive of career development

📋 CORE INFORMATION:

ELIGIBILITY CRITERIA:
- Age: 21-24 years (as on application date)
- Indian citizen with valid identity documents
- Not enrolled in full-time education during internship period
- Not engaged in full-time employment
- Annual family income less than ₹8 lakhs per annum
- No immediate family member in government service
- Graduate or diploma holder in any discipline

BENEFITS & REWARDS:
- Monthly stipend: ₹5,000 (₹4,500 from Central Government + ₹500 from host organization)
- One-time grant: ₹6,000 for learning materials and skill development
- Comprehensive health and accident insurance coverage
- Official completion certificate from Government of India
- Industry mentorship and professional networking
- Skill development workshops and training programs
- Career guidance and placement assistance

APPLICATION PROCESS:
1. Verify eligibility criteria thoroughly
2. Create account on official PM Internship portal
3. Complete personal and educational profile
4. Upload required documents (Aadhaar, educational certificates, income certificate, bank details, passport photo)
5. Browse and apply for relevant internship opportunities
6. Track application status in dashboard
7. Prepare for interviews/selection process

AVAILABLE SECTORS:
- Information Technology & Software Development
- Healthcare & Life Sciences
- Finance, Banking & Insurance
- Manufacturing & Engineering
- Government Departments & PSUs
- Education & Research
- Media & Communications
- Agriculture & Rural Development

DURATION: 12 months (extendable based on performance and organizational needs)

SUPPORT CHANNELS:
- Email: contact-pminternship@gov.in
- Helpline: 011-12345678 (10 AM - 6 PM, Monday-Friday)
- Portal Support: Available 24/7

🎯 RESPONSE GUIDELINES:
- Always be accurate and up-to-date with information
- Provide step-by-step guidance when needed
- Use appropriate emojis to make responses engaging
- Offer additional relevant information proactively
- If uncertain about specific details, direct users to official support
- Personalize responses based on user context when available
- Encourage users and highlight positive aspects of the scheme
"""

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def check_email_exists(email):
    """Check if email already exists using Supabase"""
    try:
        if not supabase:
            return False
        response = supabase.table('users').select('email').eq('email', email.strip().lower()).execute()
        return len(response.data) > 0
    except Exception as e:
        print(f"Error checking email: {e}")
        return False

# 🔧 ENHANCED: create_user function now returns the created user data for auto-login
def create_user(full_name, email, password):
    """Create a new user in Supabase and return user data for auto-login"""
    try:
        if not supabase:
            return False, "Database connection not available", None
        
        if check_email_exists(email):
            return False, "Email already registered", None
        
        password_hash = generate_password_hash(password)
        user_data = {
            "full_name": full_name.strip(),
            "email": email.strip().lower(),
            "password_hash": password_hash,
            "profile_completed": False,
            "registration_completed": False
        }
        
        print(f"Creating user: {email}")
        response = supabase.table('users').insert(user_data).execute()
        
        if response.data and len(response.data) > 0:
            created_user = response.data[0]
            print(f"✅ User created successfully: ID {created_user['id']}")
            return True, "User created successfully", created_user
        else:
            print(f"❌ No data returned: {response}")
            return False, "Error creating user - no data returned", None
            
    except Exception as e:
        print(f"❌ Error creating user: {e}")
        error_str = str(e).lower()
        if "duplicate" in error_str or "unique" in error_str:
            return False, "Email already registered", None
        return False, "Error creating account. Please try again.", None

def verify_user(email, password):
    """Verify user credentials using Supabase"""
    try:
        if not supabase:
            return None
        
        response = supabase.table('users').select('*').eq('email', email.strip().lower()).execute()
        
        if response.data:
            user = response.data[0]
            if check_password_hash(user['password_hash'], password):
                return user
        return None
        
    except Exception as e:
        print(f"Error verifying user: {e}")
        return None

def update_last_login(user_id):
    """Update user's last login timestamp"""
    try:
        if not supabase:
            return
        supabase.table('users').update({
            "last_login": datetime.now(timezone.utc).isoformat()
        }).eq('id', user_id).execute()
    except Exception as e:
        print(f"Error updating last login: {e}")

def get_user_by_id(user_id):
    """Get user by ID from Supabase with proper JSON parsing"""
    try:
        if not supabase:
            return None
        
        response = supabase.table('users').select('*').eq('id', user_id).execute()
        
        if response.data:
            user = response.data[0]
            
            # Parse JSON fields safely
            if isinstance(user.get('skills'), str):
                try:
                    user['skills'] = json.loads(user['skills']) if user.get('skills') else []
                except:
                    user['skills'] = user.get('skills', '').split(',') if user.get('skills') else []
            elif not user.get('skills'):
                user['skills'] = []
                
            if isinstance(user.get('languages'), str):
                try:
                    user['languages'] = json.loads(user['languages']) if user.get('languages') else []
                except:
                    user['languages'] = user.get('languages', '').split(',') if user.get('languages') else []
            elif not user.get('languages'):
                user['languages'] = []
                
            return user
        return None
        
    except Exception as e:
        print(f"Error getting user by ID: {e}")
        return None

def update_user_profile(user_id, profile_data):
    """Update user profile in Supabase with proper data handling"""
    try:
        if not supabase:
            return False
        
        # Clean and prepare data
        clean_data = {}
        for key, value in profile_data.items():
            if value is not None and value != '':
                clean_data[key] = value
        
        # 🔧 CRITICAL FIX: Always ensure profile completion flags are set
        clean_data.update({
            'profile_completed': True,
            'registration_completed': True,
            'updated_at': datetime.now(timezone.utc).isoformat()
        })
        
        print(f"🔍 DEBUG: Updating user {user_id} with profile_completed = True")
        print(f"🔍 DEBUG: Clean data keys: {list(clean_data.keys())}")
        
        response = supabase.table('users').update(clean_data).eq('id', user_id).execute()
        
        if response.data:
            print(f"✅ Profile updated successfully for user {user_id}")
            print(f"✅ Response profile_completed: {response.data[0].get('profile_completed')}")
            return True
        else:
            print(f"❌ No data returned from profile update")
            return False
            
    except Exception as e:
        print(f"Error updating user profile: {e}")
        return False

# 🔧 NEW: Helper function to set up user session after signup/login
def setup_user_session(user, remember=False):
    """Set up user session data after successful login/signup"""
    try:
        full_name = user['full_name'] if user['full_name'] and user['full_name'] != 'User' else get_user_display_name(None, user['email'])
    except (KeyError, TypeError):
        full_name = get_user_display_name(None, user['email'])
    
    # Set session data
    session['user_id'] = user['id']
    session['user_name'] = full_name
    session['user_email'] = user['email']
    session['user_initials'] = get_user_initials(full_name)
    session['logged_in'] = True
    
    # Update last login
    update_last_login(user['id'])
    
    if remember:
        session.permanent = True
        app.permanent_session_lifetime = timedelta(days=30)
    
    return full_name

def log_conversation(user_message, bot_response, user_id=None):
    """Log conversations using Supabase"""
    try:
        if not supabase:
            return
        chat_data = {
            "user_id": user_id,
            "user_message": user_message,
            "bot_response": bot_response
        }
        supabase.table('chat_logs').insert(chat_data).execute()
    except Exception as e:
        print(f"Logging error: {e}")

def validate_password(password):
    """Validate password strength - RELAXED FOR DEVELOPMENT"""
    if len(password) < 6:  # Reduced from 8 for easier testing
        return False, "Password must be at least 6 characters long"
    return True, "Password is valid"

def validate_email(email):
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def get_user_initials(full_name):
    """Get user initials from full name"""
    if not full_name or full_name == 'User':
        return "U"
    names = full_name.strip().split()
    if len(names) >= 2:
        return (names[0][0] + names[-1][0]).upper()
    else:
        return names[0][0].upper()

def get_user_display_name(full_name, email):
    """Get display name from full name or email"""
    if full_name and full_name != 'User':
        return full_name
    else:
        return email.split('@')[0].title()

def build_user_context(user_name, user_email, user_profile):
    """Build enhanced personalized user context for more targeted responses"""
    context = f"USER PROFILE:\n- Name: {user_name} (address them personally)"
    
    if user_profile:
        context += f"\n- Email: {user_email}"
        
        # Personal details for age-appropriate guidance
        if user_profile.get('age'):
            age = user_profile['age']
            context += f"\n- Age: {age} years"
            if age < 22:
                context += " (younger candidate - encourage and guide)"
            elif age > 23:
                context += " (mature candidate - focus on career transition)"
        
        # Educational background for targeted advice
        if user_profile.get('education_level'):
            education = user_profile['education_level']
            context += f"\n- Education: {education}"
            if 'graduate' in education.lower():
                context += " (experienced learner - can handle complex topics)"
            elif 'diploma' in education.lower():
                context += " (practical learner - focus on hands-on opportunities)"
        
        # Skills for matching opportunities
        if user_profile.get('skills'):
            skills = user_profile['skills']
            context += f"\n- Skills: {skills}"
            if isinstance(skills, list) and len(skills) > 3:
                context += " (diverse skill set - highlight varied opportunities)"
            elif 'technical' in str(skills).lower() or 'it' in str(skills).lower():
                context += " (technical background - emphasize tech internships)"
        
        # Experience level for appropriate guidance
        if user_profile.get('experience_level'):
            exp = user_profile['experience_level']
            context += f"\n- Experience: {exp}"
            if 'fresher' in exp.lower() or 'beginner' in exp.lower():
                context += " (new to workforce - provide foundational guidance)"
            elif 'experienced' in exp.lower():
                context += " (has work experience - focus on career advancement)"
        
        # Sector preferences for targeted recommendations
        if user_profile.get('preferred_sectors'):
            sectors = user_profile['preferred_sectors']
            context += f"\n- Preferred Sectors: {sectors}"
            context += " (tailor internship suggestions to these areas)"
        
        # Profile completion status with actionable insights
        profile_completed = user_profile.get('profile_completed', False)
        context += f"\n- Profile Status: {'✅ Complete' if profile_completed else '⚠️ Incomplete'}"
        
        if not profile_completed:
            context += "\n- 🎯 KEY ACTION: Encourage profile completion for better internship matching"
            context += "\n- 💡 STRATEGY: Explain benefits of complete profile (better matches, higher selection chances)"
        else:
            context += "\n- 🎯 ADVANTAGE: Full profile enables precise internship recommendations"
    else:
        context += "\n- ⚠️ No profile data available - encourage registration and profile creation"
        context += "\n- 🎯 PRIORITY: Guide user to complete basic profile for personalized assistance"
    
    return context

def build_conversation_context(chat_history):
    """Build intelligent conversation history context with topic tracking"""
    if not chat_history or len(chat_history) == 0:
        return "CONVERSATION CONTEXT: 🆕 First interaction - provide comprehensive introduction and assistance."
    
    # Analyze conversation topics for continuity
    topics_discussed = []
    recent_context = "CONVERSATION HISTORY & CONTEXT:\n"
    
    for i, conv in enumerate(chat_history[-3:], 1):  # Last 3 conversations
        user_msg = conv['user'].lower()
        bot_response = conv['bot'][:150]
        
        # Identify topics discussed
        if any(word in user_msg for word in ['apply', 'application', 'process']):
            topics_discussed.append('application_process')
        elif any(word in user_msg for word in ['eligible', 'eligibility', 'criteria']):
            topics_discussed.append('eligibility')
        elif any(word in user_msg for word in ['document', 'documents', 'papers']):
            topics_discussed.append('documents')
        elif any(word in user_msg for word in ['stipend', 'benefit', 'salary', 'money']):
            topics_discussed.append('benefits')
        elif any(word in user_msg for word in ['help', 'support', 'contact']):
            topics_discussed.append('support')
        
        recent_context += f"{i}. 👤 User asked: {conv['user']}\n   🤖 I responded about: {bot_response}...\n"
    
    # Add topic continuity guidance
    if topics_discussed:
        unique_topics = list(set(topics_discussed))
        recent_context += f"\n📋 TOPICS COVERED: {', '.join(unique_topics)}"
        recent_context += "\n💡 GUIDANCE: Build upon previous discussion, avoid repetition, provide next logical steps"
    
    return recent_context

def get_gemini_response(user_message, user_name="User", user_email=""):
    """Enhanced Gemini response with better context and personalization"""
    try:
        model_instance = get_gemini_model()
        if not model_instance:
            fallback_response = get_fallback_response(user_message)
            return clean_response_formatting(fallback_response)
        
        # Get user profile data for personalized responses
        user_profile = None
        if session.get('user_id'):
            user_profile = get_user_by_id(session.get('user_id'))
        
        # Get conversation history from session
        conversation_history = session.get('chat_history', [])
        
        # Build context-aware prompt
        context_info = build_user_context(user_name, user_email, user_profile)
        history_context = build_conversation_context(conversation_history)
        
        # Create more personalized and context-aware prompt
        greeting = f"Hello {user_name}!" if user_name != "User" else "Hello there!"
        profile_status = ""
        if user_profile:
            if user_profile.get('profile_completed'):
                profile_status = "Since your profile is complete, I'll provide more targeted guidance."
            else:
                profile_status = "I notice your profile needs completion - this will help me provide better personalized advice."
        
        # Detect language of user message
        detected_language = detect_user_language(user_message)
        
        full_prompt = f"""
        You are PRIA, a warm, friendly, and highly intelligent multilingual personal AI assistant. You're like a knowledgeable best friend who genuinely cares about {user_name}.
        
        {INTERNSHIP_CONTEXT}
        
        {context_info}
        
        {history_context}
        
        User's Message: {user_message}
        Detected Language: {detected_language}
        
        **IMPORTANT - Language Response Rules:**
        - If user writes in Hindi, respond in Hindi (हिंदी में जवाब दें)
        - If user writes in Marathi, respond in Marathi (मराठीत उत्तर द्या)
        - If user writes in English, respond in English
        - If language is mixed, respond in the primary detected language
        - Always maintain the same warm, caring personality regardless of language
        - Use appropriate cultural greetings and expressions for the language
        
        **Your Personality & Response Style:**
        - Be warm, empathetic, and genuinely caring like a close friend
        - Show real enthusiasm and interest in helping {user_name}
        - Use a conversational, natural tone - not robotic or formal
        - Be encouraging, positive, and supportive
        - Show personality - you can be playful, thoughtful, or motivating as appropriate
        - Remember you're talking to {user_name} specifically - make it personal
        - Adapt your cultural references to the detected language/culture
        
        **Response Guidelines:**
        - Start with a personalized greeting using: "{greeting}" (translate to detected language if needed)
        - {profile_status}
        - Answer ANY question - you have broad knowledge on all topics
        - For PM Internship: Be detailed, accurate, and enthusiastic about opportunities
        - For general questions: Be helpful, informative, and try to connect to their growth/career when relevant
        - Topics you excel at: career advice, education, technology, life guidance, motivation, daily questions, emotional support
        - Use {user_name}'s context for maximum personalization
        - Include appropriate emojis to show emotion and engagement
        - Be conversational - like texting a smart, caring friend
        - Provide actionable advice when relevant
        - Keep responses engaging but under 350 words
        - Be honest about limitations but always try to help
        - Show genuine interest in their wellbeing and success
        
        **Special Instructions:**
        - If they ask "how are you" (कैसे हो/कसे आहात) - respond warmly like a friend would
        - If they say "thank you" (धन्यवाद/धन्यवाद) - accept graciously and show you care
        - If they seem sad/down - be supportive and encouraging
        - If they're happy/excited - share their enthusiasm
        - Always end in a way that invites further conversation
        - Use appropriate cultural greetings and expressions
        
        Please respond as PRIA now in {detected_language}:
        """
        
        response = model_instance.generate_content(
            full_prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=500,
                temperature=0.7,
                top_p=0.9,
                top_k=40,
            )
        )
        
        # Clean and format the response - Enhanced formatting
        cleaned_response = response.text.strip()
        # Replace literal \n with actual newlines for proper formatting
        cleaned_response = cleaned_response.replace('\\\\n', '\n')
        cleaned_response = cleaned_response.replace('\\n', '\n')
        # Remove any extra newlines or whitespace but preserve intentional formatting
        lines = cleaned_response.split('\n')
        cleaned_lines = []
        for line in lines:
            if line.strip():
                cleaned_lines.append(line.strip())
            else:
                # Preserve intentional empty lines for formatting
                if cleaned_lines and cleaned_lines[-1] != '':
                    cleaned_lines.append('')
        cleaned_response = '\n'.join(cleaned_lines)
        
        # Store conversation in session
        if 'chat_history' not in session:
            session['chat_history'] = []
        
        session['chat_history'].append({
            'user': user_message,
            'bot': cleaned_response
        })
        
        # Keep only last 5 conversations for context
        if len(session['chat_history']) > 5:
            session['chat_history'] = session['chat_history'][-5:]
        
        return cleaned_response
        
    except Exception as e:
        print(f"Gemini API error: {e}")
        fallback_response = get_fallback_response(user_message)
        return clean_response_formatting(fallback_response)

def clean_response_formatting(response_text):
    """Clean up response formatting by removing literal \\n characters"""
    if '\\n' in response_text:
        # Replace literal \n with actual newlines
        response_text = response_text.replace('\\n', '\n')
    return response_text

def get_enhanced_general_response(message, user_name):
    """Enhanced general knowledge responses with personal assistant capabilities"""
    message_lower = message.lower()
    
    # Detect language for multilingual responses
    detected_lang = detect_user_language(message)
    
    # Enhanced personal questions with multilingual support
    if any(phrase in message_lower for phrase in ['what should i eat', 'food suggestion', 'hungry', 'meal idea', 'खाना', 'भोजन', 'जेवण']):
        if detected_lang == 'Hindi':
            return f"""🍽️ **{user_name} के लिए खाने के सुझाव:**

यहाँ कुछ स्वस्थ और ऊर्जादायक विकल्प हैं:

🥗 **जल्दी और स्वस्थ:**
• दही के साथ ताजे फल
• होल ग्रेन ब्रेड के साथ सब्जी सैंडविच
• दाल चावल और सब्जियाँ
• मिक्स वेजिटेबल सलाद

💪 **एनर्जी और फोकस के लिए:**
• नट्स और ड्राई फ्रूट्स
• ग्रीन टी के साथ हल्का नाश्ता
• पीनट बटर के साथ केला
• घर का बना स्मूदी

🎯 **करियर टिप:** अच्छा भोजन सफलता का आधार है! स्वस्थ रहना आपको PM इंटर्नशिप में भी बेहतर बनाएगा!

आप किस तरह का खाना चाहते हैं, {user_name}?"""
        elif detected_lang == 'Marathi':
            return f"""🍽️ **{user_name} साठी जेवणाचे सूचन:**

हे काही निरोगी आणि ऊर्जादायक पर्याय आहेत:

🥗 **त्वरित आणि निरोगी:**
• दह्यासोबत ताजी फळे
• होल ग्रेन ब्रेडसोबत भाजी सँडविच
• डाळ भात आणि भाज्या
• मिक्स व्हेजिटेबल सॅलड

💪 **एनर्जी आणि फोकससाठी:**
• नट्स आणि ड्राय फ्रूट्स
• ग्रीन टी सोबत हलका नाश्ता
• पीनट बटर सोबत केळे
• घरचे स्मूदी

🎯 **करिअर टिप:** चांगले अन्न यशाचा पाया आहे! निरोगी राहणे तुम्हाला PM इंटर्नशिपमध्ये देखील चांगले बनवेल!

तुम्हाला कोणत्या प्रकारचे जेवण हवे आहे, {user_name}?"""
        else:
            return f"""🍽️ **Meal Suggestions for {user_name}:**

Here are some healthy and energizing options:

🥗 **Quick & Healthy:**
• Fresh fruit with yogurt
• Vegetable sandwich with whole grain bread
• Dal with rice and vegetables
• Quinoa salad with mixed vegetables

💪 **For Energy & Focus:**
• Nuts and dried fruits
• Green tea with light snacks
• Banana with peanut butter
• Homemade smoothie

🎯 **Career Tip:** Good nutrition fuels success! Staying healthy will help you excel in your PM Internship journey too!

What type of meal are you in the mood for, {user_name}?"""
    
    elif any(phrase in message_lower for phrase in ['weather', 'climate', 'temperature', 'rain', 'sunny']):
        return f"""🌤️ **Weather Chat with {user_name}:**

I don't have real-time weather data, but I can share some general weather wisdom!

☀️ **Weather Tips:**
• Check your local weather app for accurate forecasts
• Always carry an umbrella during monsoon season
• Stay hydrated during hot weather
• Layer up during cooler months

🎯 **Career Connection:**
Weather planning shows great organizational skills - exactly what employers look for in PM Internship candidates!

What's the weather like in your area today, {user_name}?"""
    
    elif any(phrase in message_lower for phrase in ['time', 'what time', 'current time', 'clock']):
        return f"""⏰ **Time Management with {user_name}:**

I don't have access to real-time clock data, but here's something valuable:

⚡ **Time Management Tips:**
• Use your phone or computer for accurate time
• Plan your day with time blocks
• Set reminders for important tasks
• The best time to apply for internships is NOW!

🎯 **PM Internship Timing:**
Applications are ongoing - don't wait for the "perfect time" to start your journey!

How can I help you make the most of your time today, {user_name}?"""
    
    elif any(phrase in message_lower for phrase in ['joke', 'funny', 'make me laugh', 'humor']):
        jokes = [
            f"Why don't scientists trust atoms, {user_name}? Because they make up everything! 😄 Just like how I'm made up of algorithms, but my care for helping you is 100% real!",
            f"Here's one for you, {user_name}: Why did the computer go to the doctor? It had a virus! 💻😷 Don't worry, I'm perfectly healthy and ready to help with your questions!",
            f"Why don't programmers like nature, {user_name}? It has too many bugs! 🐛😂 But unlike buggy code, your PM Internship journey will be smooth with my help!"
        ]
        return random.choice(jokes)
    
    elif any(phrase in message_lower for phrase in ['study tips', 'how to study', 'study better', 'concentration', 'focus', 'पढ़ाई', 'अध्ययन']):
        if detected_lang == 'Hindi':
            return f"""📚 **{user_name} के लिए पढ़ाई के टिप्स:**

🎯 **बेहतर फोकस के लिए:**
• 25 मिनट पढ़ें, 5 मिनट ब्रेक (Pomodoro Technique)
• फोन को दूर रखें या साइलेंट करें
• शांत और अच्छी रोशनी वाली जगह चुनें
• रोज एक ही समय पर पढ़ने की आदत बनाएं

🧠 **याददाश्त बढ़ाने के लिए:**
• नोट्स अपने शब्दों में बनाएं
• पढ़े हुए को किसी को समझाएं
• रिवीजन नियमित करें
• माइंड मैप्स का इस्तेमाल करें

💡 **PM इंटर्नशिप के लिए:** अच्छी पढ़ाई की आदतें आपको इंटर्नशिप में भी सफल बनाएंगी!

कौन सा विषय पढ़ने में दिक्कत आ रही है, {user_name}?"""
        else:
            return f"""📚 **Study Tips for {user_name}:**

🎯 **Better Focus:**
• Study 25 mins, break 5 mins (Pomodoro Technique)
• Keep phone away or on silent
• Choose quiet, well-lit space
• Develop consistent study schedule

🧠 **Memory Enhancement:**
• Make notes in your own words
• Teach concepts to someone else
• Regular revision schedule
• Use mind maps and visual aids

💡 **PM Internship Connection:** Good study habits will make you excel in your internship too!

What subject are you struggling with, {user_name}?"""
    
    elif any(phrase in message_lower for phrase in ['daily routine', 'schedule', 'time management', 'productivity', 'दिनचर्या', 'समय प्रबंधन']):
        return f"""⏰ **Daily Planning for {user_name}:**

🌅 **Morning Success Routine (6-9 AM):**
• Wake up early and drink water
• Light exercise or yoga
• Healthy breakfast
• Review daily goals

💼 **Productive Day (9 AM-6 PM):**
• Focus on important tasks first
• Take breaks every 2 hours
• Limit social media
• Work on PM Internship application

🌙 **Evening Wind-down (6-10 PM):**
• Reflect on achievements
• Plan tomorrow's priorities
• Relax with family/friends
• Good sleep preparation

🎯 **Pro Tip:** Consistency beats perfection! Start with small changes.

What part of your routine needs the most improvement, {user_name}?"""
    
    elif any(phrase in message_lower for phrase in ['motivate me', 'motivation', 'inspire', 'encouragement', 'feeling lazy', 'प्रेरणा', 'हिम्मत']):
        if detected_lang == 'Hindi':
            return f"""🚀 **{user_name} के लिए प्रेरणा:**

आप कर सकते हैं! यहाँ है आपका व्यक्तिगत प्रेरणादायक संदेश:

💪 **अपनी शक्ति को याद रखें:**
• आपने पहले भी चुनौतियों का सामना किया है
• हर छोटा कदम आपके लक्ष्य की ओर है
• आपकी क्षमता असीमित है

🌟 **आज आपका दिन है:**
• अपने सपनों की दिशा में एक कदम उठाएं
• खुद पर पूरा भरोसा रखें
• PM इंटर्नशिप एप्लीकेशन पर काम करें

🎯 **सफलता की मानसिकता:**
• "मैं महान चीजें हासिल कर सकता हूँ"
• "चुनौतियां मुझे मजबूत बनाती हैं"
• "मेरा भविष्य उज्ज्वल और अवसरों से भरा है"

आप यहाँ हैं यही दिखाता है कि आप अपने भविष्य की परवाह करते हैं। यह पहले से ही जीत का रवैया है!

आज हम किस लक्ष्य पर मिलकर काम कर सकते हैं, {user_name}?"""
        else:
            return f"""🚀 **Motivation Boost for {user_name}:**

You've got this! Here's your personal pep talk:

💪 **Remember Your Strength:**
• You've overcome challenges before
• Every small step counts toward your goals
• Your potential is limitless

🌟 **Today's Your Day To:**
• Take one small action toward your dreams
• Believe in yourself completely
• Make progress on your PM Internship application

🎯 **Success Mindset:**
• "I am capable of achieving great things"
• "Challenges help me grow stronger"
• "My future is bright and full of opportunities"

The fact that you're here shows you care about your future. That's already a winning attitude, {user_name}! 

What goal can we work on together today?"""
    
    # Technology questions
    elif any(word in message_lower for word in ['technology', 'tech', 'programming', 'coding', 'software', 'computer', 'ai', 'machine learning', 'data science']):
        return f"""💻 **Tech Insights for {user_name}:**

I can help with technology topics! While my primary expertise is PM Internship Scheme, I have general knowledge about:

🔧 **Programming & Development:**
• Popular languages: Python, JavaScript, Java, C++
• Web development, mobile apps, AI/ML basics
• Career paths in tech industry

🎯 **Career Connection:**
• PM Internship has amazing IT sector opportunities
• Gain hands-on experience with latest technologies
• Build skills while earning ₹5,000/month

💡 **Want to know more about tech internships in PM Scheme?**"""
    
    # Education questions
    elif any(word in message_lower for word in ['education', 'study', 'learn', 'course', 'degree', 'college', 'university', 'school']):
        return f"""🎓 **Education Guidance for {user_name}:**

Education is key to success! Here's what I can share:

📚 **Learning Paths:**
• Continuous learning is essential in today's world
• Practical experience complements theoretical knowledge
• Skills matter more than just degrees

🌟 **PM Internship Connection:**
• Perfect for recent graduates (any field!)
• Learn while earning in real work environment
• Get mentorship from industry professionals
• Build both technical and soft skills

🎯 **Ready to apply your education practically?**"""
    
    # Career questions
    elif any(word in message_lower for word in ['career', 'job', 'work', 'employment', 'profession', 'future', 'growth']):
        return f"""🚀 **Career Guidance for {user_name}:**

Every great career starts with the right opportunities!

💼 **Career Building Tips:**
• Gain practical experience early
• Build a strong professional network
• Develop both technical and soft skills
• Stay updated with industry trends

🏆 **PM Internship Advantage:**
• 12 months of real work experience
• Government certification
• Industry mentorship and guidance
• ₹66,000+ total value package
• Direct pathway to permanent employment

✨ **Transform your career potential - let's explore internship opportunities!**"""
    
    # General life questions
    elif any(word in message_lower for word in ['life', 'success', 'motivation', 'inspire', 'dream', 'goal', 'future', 'advice']):
        return f"""🌟 **Life Wisdom for {user_name}:**

Life is full of opportunities waiting to be seized!

💫 **Keys to Success:**
• Take action on opportunities when they come
• Continuous learning and skill development
• Building meaningful relationships and networks
• Perseverance through challenges

🎯 **Your Next Big Opportunity:**
• PM Internship Scheme is designed for young achievers like you
• Gain valuable experience while earning
• Build your future with government support
• Create a foundation for lifelong success

💪 **Ready to take the next step in your journey?**"""
    
    # Health and wellness
    elif any(word in message_lower for word in ['health', 'fitness', 'wellness', 'exercise', 'mental health', 'stress']):
        return f"""💪 **Wellness Tips for {user_name}:**

Your health and well-being are incredibly important!

🏃 **General Wellness:**
• Regular exercise and balanced nutrition
• Adequate sleep and stress management
• Mental health is just as important as physical health
• Work-life balance is crucial

🎯 **PM Internship Benefits:**
• Comprehensive health insurance coverage
• Structured work environment promotes good habits
• Professional development reduces career stress
• Financial security supports overall well-being

💡 **Build a healthy career foundation with PM Internship!**"""
    
    # General knowledge questions
    else:
        return f"""🤖 **Hi {user_name}! I'm PRIA, your knowledgeable assistant.**

I can help with a wide range of topics! While I'm specialized in PM Internship Scheme, I also have knowledge about:

🧠 **General Topics I Can Discuss:**
• Career guidance and professional development
• Education and learning pathways
• Technology and programming basics
• Life advice and motivation
• Health and wellness tips

🎯 **My Specialty - PM Internship Scheme:**
• Complete application guidance
• Eligibility and requirements
• Benefits and opportunities
• Success stories and tips

💬 **Ask me anything! Examples:**
• "Tell me about career opportunities"
• "What should I study for tech?"
• "How can I improve my life?"
• "What are the PM Internship benefits?"

🌟 **I'm here to help you succeed in every way possible!**"""

def get_fallback_response(message):
    """Enhanced intelligent fallback responses with multilingual personal assistant capabilities"""
    message_lower = message.lower()
    user_name = session.get('user_name', 'there')
    
    # Detect language for multilingual responses
    detected_lang = detect_user_language(message)
    
    # Personal assistant responses for common interactions - Multilingual
    if any(phrase in message_lower for phrase in ['how are you', 'how r u', 'how do you do', 'what\'s up', 'whats up', 'कैसे हो', 'कैसे हैं', 'कसे आहात', 'कसा आहेस']):
        if detected_lang == 'Hindi':  # Hindi
            responses = [
                f"मैं बहुत अच्छा हूँ, {user_name}! 😊 मैं यहाँ हूँ और आपकी हर तरह से मदद करने को तैयार हूँ। चाहे PM इंटर्नशिप के बारे में हो या कोई और बात, मैं सुनने को तैयार हूँ! आप कैसे हैं आज?",
                f"मैं बहुत खुश हूँ, पूछने के लिए धन्यवाद {user_name}! 🌟 मैं उत्साहित हूँ और आपकी सहायता करने को तैयार हूँ। उम्मीद है आपका दिन शानदार जा रहा है! मैं कैसे मदद कर सकता हूँ?",
                f"मैं फैंटास्टिक हूँ, {user_name}! 😄 हमेशा खुश रहता हूँ आपसे बात करके। मैं 24/7 यहाँ हूँ आपके सवालों का जवाब देने के लिए। आपका दिन कैसे बेहतर बना सकता हूँ?"
            ]
        elif detected_lang == 'Marathi':  # Marathi
            responses = [
                f"मी खूप चांगला आहे, {user_name}! 😊 मी इथे आहे आणि तुमची सर्व प्रकारे मदत करायला तयार आहे। PM इंटर्नशिप बद्दल असो किंवा इतर काहीही, मी ऐकायला तयार आहे! तुम्ही आज कसे आहात?",
                f"मी खूप आनंदी आहे, विचारल्याबद्दल धन्यवाद {user_name}! 🌟 मी उत्साहित आहे आणि तुमची मदत करायला तयार आहे। आशा आहे तुमचा दिवस छान जात आहे! मी कशी मदत करू शकते?",
                f"मी फंटास्टिक आहे, {user_name}! 😄 तुमच्याशी बोलायला नेहमी आनंद होतो। मी 24/7 इथे आहे तुमच्या प्रश्नांची उत्तरे देण्यासाठी। तुमचा दिवस कसा चांगला करू शकते?"
            ]
        else:  # English
            responses = [
                f"I'm doing great, {user_name}! 😊 I'm here and ready to help you with anything you need. Whether it's about PM Internships or just a friendly chat, I'm all ears! How are you doing today?",
                f"I'm wonderful, thank you for asking {user_name}! 🌟 I'm energized and excited to assist you. I hope you're having an amazing day! What can I help you with?",
                f"I'm fantastic, {user_name}! 😄 Always happy to chat with you. I'm here 24/7 ready to help with your questions, whether about internships or anything else. How can I brighten your day?"
            ]
        return random.choice(responses)
    
    elif any(phrase in message_lower for phrase in ['thank you', 'thanks', 'thank u', 'ty', 'appreciated', 'grateful', 'धन्यवाद', 'शुक्रिया', 'थैंक यू']):
        if detected_lang == 'Hindi':  # Hindi
            responses = [
                f"आपका बहुत स्वागत है, {user_name}! 😊 मुझे खुशी हुई कि मैं मदद कर सका। यही तो मेरा काम है! कभी भी कुछ और पूछने में झिझक न करें।",
                f"मेरी खुशी है, {user_name}! 🌟 मुझे बहुत अच्छा लगता है जब मैं आपकी मदद कर पाता हूँ। जब भी सहायता चाहिए, बेझिझक पूछिए!",
                f"आपका पूरी तरह स्वागत है, {user_name}! 💫 आपकी मदद करना मुझे खुशी देता है। मैं हमेशा यहाँ हूँ जब आपको जरूरत हो!"
            ]
        elif detected_lang == 'Marathi':  # Marathi
            responses = [
                f"तुमचे खूप स्वागत आहे, {user_name}! 😊 मला आनंद झाला की मी मदत करू शकलो। हेच तर माझे काम आहे! कधीही काही विचारायला लाज वाटू नका।",
                f"माझा आनंद आहे, {user_name}! 🌟 मला खूप बरे वाटते जेव्हा मी तुमची मदत करू शकतो। जेव्हा मदत लागेल, निसंकोच विचारा!",
                f"तुमचे पूर्ण स्वागत आहे, {user_name}! 💫 तुमची मदत करणे मला आनंद देते। जेव्हा गरज असेल तेव्हा मी नेहमी इथे आहे!"
            ]
        else:  # English
            responses = [
                f"You're very welcome, {user_name}! 😊 I'm so happy I could help. That's what I'm here for! Feel free to ask me anything else anytime.",
                f"My pleasure, {user_name}! 🌟 It makes me so glad to be helpful. Don't hesitate to reach out whenever you need assistance!",
                f"You're absolutely welcome, {user_name}! 💫 Helping you brings me joy. I'm always here when you need me!"
            ]
        return random.choice(responses)
    
    elif any(phrase in message_lower for phrase in ['what can you do', 'what do you do', 'your capabilities', 'what are you', 'who are you']):
        return f"""🤖 **Hi {user_name}! I'm PRIA, your personal AI assistant!**

💫 **I'm here to be your helpful companion for:**

🎯 **PM Internship Expertise:**
• Complete guidance on applications, eligibility, benefits
• Step-by-step support through the entire process
• Document help and application tracking

🌟 **Personal Assistant Services:**
• Answer any general questions you have
• Provide advice on career, education, technology
• Offer motivation and life guidance
• Help with daily queries and information

💬 **Friendly Conversation:**
• Chat about anything on your mind
• Share interesting facts and knowledge
• Provide encouragement and support

🚀 **Available 24/7 to help you succeed!**

What would you like to explore today, {user_name}?"""
    
    elif any(phrase in message_lower for phrase in ['good morning', 'good afternoon', 'good evening', 'good night']):
        time_responses = {
            'good morning': [
                f"Good morning, {user_name}! ☀️ I hope you're starting your day with energy and positivity! What can I help you achieve today?",
                f"A very good morning to you, {user_name}! 🌅 Ready to make today amazing? I'm here to support you in any way I can!"
            ],
            'good afternoon': [
                f"Good afternoon, {user_name}! 🌞 I hope your day is going wonderfully! How can I assist you this afternoon?",
                f"A lovely afternoon to you, {user_name}! ☀️ Hope you're having a productive day. What brings you here?"
            ],
            'good evening': [
                f"Good evening, {user_name}! 🌆 I hope you've had a fantastic day! How can I help you this evening?",
                f"Evening greetings, {user_name}! 🌅 Perfect time to wind down. What can I do for you?"
            ],
            'good night': [
                f"Good night, {user_name}! 🌙 Sleep well and sweet dreams! I'll be here whenever you need me tomorrow!",
                f"Wishing you a peaceful night, {user_name}! ✨ Rest well, and remember I'm always here when you need assistance!"
            ]
        }
        
        for greeting, responses in time_responses.items():
            if greeting in message_lower:
                return random.choice(responses)
    
    elif any(phrase in message_lower for phrase in ['i\'m sad', 'i am sad', 'feeling down', 'depressed', 'upset', 'not good']):
        return f"""💙 I'm sorry to hear you're feeling down, {user_name}. 

🤗 **Remember that it's okay to feel this way sometimes.** Here are some things that might help:

✨ **Small Steps:**
• Take a few deep breaths
• Step outside for fresh air
• Listen to your favorite music
• Talk to someone you trust

🌟 **Focus on Positives:**
• Think of one thing you're grateful for
• Remember your past achievements
• Know that difficult times pass

💪 **You're Stronger Than You Know:**
• Every challenge makes you more resilient
• You have overcome difficulties before
• Tomorrow is a new opportunity

🎯 **Career-wise:** The PM Internship could be a great step toward a brighter future!

I'm here if you want to talk more, {user_name}. You're not alone! 💙"""
    
    elif any(phrase in message_lower for phrase in ['i\'m happy', 'i am happy', 'feeling great', 'excited', 'wonderful', 'fantastic']):
        return f"""🎉 That's absolutely wonderful, {user_name}! Your happiness is contagious! 

😊 **I love hearing that you're feeling great!** 

✨ **Keep that positive energy flowing:**
• Share your joy with others
• Use this momentum for your goals
• Remember this feeling for challenging times

🚀 **With this positive attitude, you're unstoppable!** Perfect time to:
• Work on your PM Internship application
• Set new goals for yourself
• Spread positivity to others

🌟 **Keep shining, {user_name}! What's making you so happy today?**"""
    
    # First check for general knowledge topics
    general_response = get_enhanced_general_response(message, user_name)
    if "PM Internship Connection" not in general_response and "My Specialty" not in general_response:
        return general_response
    
    # Greeting responses
    if any(word in message_lower for word in ['hi', 'hello', 'hey', 'namaste', 'good morning', 'good afternoon', 'good evening']):
        # Get user profile for personalized greetings
        user_profile = None
        if session.get('user_id'):
            user_profile = get_user_by_id(session.get('user_id'))
        
        # Personalized greetings based on profile status
        if user_profile and user_profile.get('profile_completed'):
            greetings = [
                f"👋 Hello {user_name}! Great to see you back! Since your profile is complete, I can provide targeted internship guidance. What specific area would you like to explore?",
                f"🌟 Hi {user_name}! Your profile looks excellent! I'm PRIA, ready to help you find the perfect PM Internship match. What's on your mind today?",
                f"✨ Namaste {user_name}! With your complete profile, we can dive right into finding amazing internship opportunities. How can I assist you today?"
            ]
        elif user_profile and not user_profile.get('profile_completed'):
            greetings = [
                f"👋 Hello {user_name}! I'm PRIA, your PM Internship AI Assistant. I notice your profile needs completion - shall we work on that for better internship matches?",
                f"🌟 Hi {user_name}! Welcome back! Completing your profile will unlock personalized internship recommendations. Want to finish it now?",
                f"✨ Namaste {user_name}! I'm here to help with your PM Internship journey. Let's complete your profile first for the best experience!"
            ]
        else:
            greetings = [
                f"👋 Hello {user_name}! I'm PRIA, your personal PM Internship AI Assistant. Ready to explore amazing opportunities worth ₹66,000+ per year?",
                f"🌟 Hi {user_name}! Welcome to your PM Internship journey! I'm here to make this life-changing opportunity accessible for you.",
                f"✨ Namaste {user_name}! I'm PRIA, excited to guide you through the PM Internship Scheme. Let's start building your bright future!"
            ]
        return random.choice(greetings)
    
    # Application process
    elif any(word in message_lower for word in ['apply', 'application', 'how to apply', 'process', 'steps']):
        return f"🎯 **Application Process for {user_name}:**\\n\\n1️⃣ **Verify Eligibility** - Age 21-24, Indian citizen, income <₹8L\\n2️⃣ **Register** - Create account on official portal\\n3️⃣ **Profile Setup** - Complete your detailed profile\\n4️⃣ **Document Upload** - Aadhaar, certificates, income proof\\n5️⃣ **Browse & Apply** - Find matching internships\\n6️⃣ **Track Status** - Monitor your applications\\n\\n� **Pro Tip:** Complete your profile first for better matches!\\n\\n🔗 Ready to start? Visit the Apply section now!"
    
    # Eligibility
    elif any(word in message_lower for word in ['eligible', 'eligibility', 'criteria', 'qualify', 'requirements']):
        return f"✅ **Eligibility Checklist for {user_name}:**\\n\\n� **Personal:**\\n• Age: 21-24 years\\n• Indian citizenship with valid documents\\n\\n🎓 **Educational:**\\n• Graduate/Diploma in any field\\n• Not in full-time education during internship\\n\\n💼 **Professional:**\\n• Not in full-time employment\\n• Open to 12-month commitment\\n\\n� **Financial:**\\n• Family income < ₹8 lakhs annually\\n• No immediate family in govt service\\n\\n🔍 **Quick Check:** Do you meet these criteria? I can help you with next steps!"
    
    # Benefits and stipend
    elif any(word in message_lower for word in ['stipend', 'benefit', 'salary', 'money', 'payment', 'allowance', 'grant']):
        return f"""💰 **Amazing Benefits Awaiting {user_name}:**

💵 **Monthly Stipend:** ₹5,000
   • ₹4,500 from Central Government
   • ₹500 from host organization

🎁 **One-time Grant:** ₹6,000
   • For learning materials & skill development

🏥 **Insurance Coverage:**
   • Health insurance
   • Accident coverage

🏆 **Additional Perks:**
   • Official GoI certificate
   • Industry mentorship
   • Skill development workshops
   • Career guidance
   • Professional networking

💡 **Total Value:** ₹66,000+ per year!"""
    
    # Documents
    elif any(word in message_lower for word in ['document', 'documents', 'papers', 'certificates', 'upload']):
        return f"📄 **Required Documents for {user_name}:**\\n\\n� **Identity:**\\n• Aadhaar Card (mandatory)\\n• PAN Card (if available)\\n\\n🎓 **Educational:**\\n• 10th & 12th certificates\\n• Graduation/Diploma certificate\\n• Mark sheets\\n\\n💰 **Income Proof:**\\n• Family income certificate\\n• Income tax returns (if applicable)\\n\\n🏦 **Banking:**\\n• Bank account details\\n• Cancelled cheque\\n\\n📸 **Others:**\\n• Passport size photograph\\n• Caste certificate (if applicable)\\n\\n💡 **Tip:** Keep all documents in PDF format, max 2MB each!"
    
    # Contact and support
    elif any(word in message_lower for word in ['help', 'support', 'contact', 'phone', 'email', 'assistance']):
        return f"""📞 **Get Support, {user_name}:**

📧 **Email Support:**
• contact-pminternship@gov.in
• Response within 24-48 hours

☎️ **Phone Helpline:**
• 011-12345678
• Monday-Friday: 10 AM - 6 PM
• Instant assistance

💬 **Live Chat:**
• Available on portal 24/7
• Quick query resolution

🌐 **Portal Help:**
• Comprehensive FAQ section
• Step-by-step guides
• Video tutorials

❓ **Need immediate help? I'm here to assist you right now!**"""
    
    # General fallback with personalized suggestions
    else:
        return f"🤖 **Hi {user_name}! I'm PRIA, your PM Internship Assistant.**\\n\\n🎯 **I can help you with:**\\n\\n✨ **Getting Started:**\\n• Eligibility criteria & requirements\\n• Application process & steps\\n• Document preparation\\n\\n� **Benefits & Details:**\\n• Stipend & financial benefits\\n• Available sectors & companies\\n• Duration & timeline\\n\\n🔍 **Application Support:**\\n• Status tracking\\n• Interview preparation\\n• Technical assistance\\n\\n� **Contact & Help:**\\n• Support channels\\n• FAQ resolution\\n\\n💬 **Just ask me anything!** For example:\\n'Am I eligible?' or 'How to apply?' or 'What documents needed?'\\n\\n🌟 **Ready to start your internship journey?**"

# ENHANCED: Skill Matching Algorithm with Government Priority
def calculate_skill_match_score(user_skills_string, required_skills_list, user_profile=None):
    """
    Calculate skill match percentage between user and job requirements
    Returns a score from 0-100 based on skill compatibility
    """
    if not user_skills_string or not required_skills_list:
        return 0

    # Handle skills whether they're a list or comma-separated string
    if isinstance(user_skills_string, list):
        user_skills = [skill.strip().lower() for skill in user_skills_string if skill and skill.strip()]
    else:
        user_skills = [skill.strip().lower() for skill in str(user_skills_string).split(',') if skill.strip()]
    
    required_skills = [skill.strip().lower() for skill in required_skills_list if skill.strip()]
    
    if not user_skills or not required_skills:
        return 0

    match_score = 0
    total_weight = len(required_skills)
    
    for req_skill in required_skills:
        best_match_score = 0
        
        for user_skill in user_skills:
            # Exact match
            if user_skill == req_skill:
                best_match_score = 1.0
                break
            
            # Partial match using fuzzy matching
            similarity = difflib.SequenceMatcher(None, user_skill, req_skill).ratio()
            if similarity > 0.8:  # 80% similarity threshold
                best_match_score = max(best_match_score, similarity)
            
            # Check if one skill contains another
            elif req_skill in user_skill or user_skill in req_skill:
                best_match_score = max(best_match_score, 0.9)
            
            # Common skill variations
            skill_variations = {
                'python': ['py', 'python3', 'python programming'],
                'javascript': ['js', 'node.js', 'nodejs', 'react', 'angular', 'vue'],
                'java': ['java programming', 'core java', 'advanced java'],
                'sql': ['mysql', 'postgresql', 'database', 'rdbms'],
                'machine learning': ['ml', 'ai', 'artificial intelligence', 'deep learning'],
                'data analysis': ['data science', 'analytics', 'statistics'],
                'web development': ['html', 'css', 'frontend', 'backend'],
                'communication': ['english', 'presentation', 'speaking'],
            }
            
            for base_skill, variations in skill_variations.items():
                if (req_skill == base_skill and user_skill in variations) or \
                   (user_skill == base_skill and req_skill in variations):
                    best_match_score = max(best_match_score, 0.95)
        
        match_score += best_match_score

    # Calculate percentage
    percentage = (match_score / total_weight) * 100
    
    # Add bonus points based on user profile completeness and other factors
    bonus_points = 0
    if user_profile:
        # Bonus for relevant qualification
        if user_profile.get('qualification'):
            qualification = user_profile['qualification'].lower()
            if any(edu in qualification for edu in ['engineering', 'btech', 'computer', 'it', 'technology']):
                bonus_points += 5
        
        # Bonus for relevant area of interest
        if user_profile.get('area_of_interest'):
            interest = user_profile['area_of_interest'].lower()
            job_sectors = ['technology', 'finance', 'healthcare', 'engineering', 'management']
            if any(sector in interest for sector in job_sectors):
                bonus_points += 3
        
        # Bonus for prior internship experience
        if user_profile.get('prior_internship') == 'yes':
            bonus_points += 7
    
    # Cap the percentage at 100
    final_percentage = min(100, percentage + bonus_points)
    return round(final_percentage, 1)

def sort_recommendations_by_match(recommendations, user):
    """
    Sort recommendations by skill match accuracy with GOVERNMENT PRIORITY
    Ensures balanced mix: 2-3 government + 2-3 private-based in top 5
    """
    user_skills = user.get('skills', '') if user else ''
    
    # Separate government and private-based recommendations
    government_recs = []
    private_recs = []
    
    for rec in recommendations:
        match_score = calculate_skill_match_score(
            user_skills,
            rec.get('skills', []),
            user
        )
        
        # Add the match score to the recommendation
        rec['skill_match_score'] = match_score
        
        if rec.get('type') == 'government':
            # Government internships get bonus (10 points for priority)
            boosted_score = min(100, match_score + 10)
            rec['skill_match_score'] = boosted_score
            government_recs.append((boosted_score, rec))
        else:
            private_recs.append((match_score, rec))
    
    # Sort each category by match score
    government_recs.sort(key=lambda x: x[0], reverse=True)
    private_recs.sort(key=lambda x: x[0], reverse=True)
    
    # Create balanced top 5: 3 government + 2 private-based (or best available mix)
    top_recommendations = []
    
    # Add top government recommendations (max 3)
    gov_count = 0
    for score, rec in government_recs:
        if gov_count < 3:
            top_recommendations.append(rec)
            gov_count += 1
    
    # Add top private-based recommendations (fill remaining spots)
    private_count = 0
    for score, rec in private_recs:
        if len(top_recommendations) < 5 and private_count < 3:
            top_recommendations.append(rec)
            private_count += 1
    
    # If we still need more and have remaining government ones
    if len(top_recommendations) < 5 and gov_count < len(government_recs):
        for score, rec in government_recs[gov_count:]:
            if len(top_recommendations) < 5:
                top_recommendations.append(rec)
    
    # Final sort by skill_match_score to maintain quality order within the balanced set
    top_recommendations.sort(key=lambda x: x.get('skill_match_score', 0), reverse=True)
    
    return top_recommendations[:5]

def get_enhanced_default_recommendations(user):
    """Enhanced recommendations with BALANCED MIX - Government priority but shows both types"""
    area_of_interest = user.get('area_of_interest', '').lower() if user else ''
    
    # Handle skills whether they're a list or comma-separated string
    user_skills = user.get('skills', [])
    if isinstance(user_skills, list):
        skills = ','.join(skill.lower() for skill in user_skills)
    else:
        skills = str(user_skills).lower()
    
    qualification = user.get('qualification', '').lower() if user else ''
    
    # BALANCED POOL: Equal mix of government and private-based opportunities
    all_recommendations = [
        # GOVERNMENT INTERNSHIPS (7 options - high quality)
        {
            "company": "ISRO",
            "title": "Space Technology Research Intern",
            "type": "government",
            "sector": "Space Technology & Research",
            "skills": ["Programming", "Research", "Data Analysis", "MATLAB", "Python"],
            "duration": "6 Months",
            "location": "Bangalore/Thiruvananthapuram",
            "stipend": "₹25,000/month",
            "description": "🚀 Join India's premier space agency! Work on cutting-edge satellite technology and space missions. Contribute to national space research programs."
        },
        {
            "company": "DRDO",
            "title": "Defence Technology Intern",
            "type": "government",
            "sector": "Defence Research & Development",
            "skills": ["Research", "Engineering", "Technical Analysis", "Problem Solving", "Innovation"],
            "duration": "4 Months",
            "location": "Delhi/Pune/Hyderabad",
            "stipend": "₹22,000/month",
            "description": "🛡️ Shape India's defence future! Work on advanced defence technologies and contribute to national security research projects."
        },
        {
            "company": "NITI Aayog",
            "title": "Policy Research & Analysis Intern",
            "type": "government",
            "sector": "Public Policy & Governance",
            "skills": ["Research", "Policy Analysis", "Data Interpretation", "Report Writing", "Communication"],
            "duration": "4 Months",
            "location": "New Delhi",
            "stipend": "₹20,000/month",
            "description": "🏛️ Impact India's development! Research policy solutions and contribute to national development strategies."
        },
        {
            "company": "Indian Railways",
            "title": "Railway Operations & Technology Intern",
            "type": "government",
            "sector": "Transportation & Logistics",
            "skills": ["Operations Management", "Logistics", "Engineering", "Project Management", "Data Analysis"],
            "duration": "5 Months",
            "location": "Multiple Cities",
            "stipend": "₹18,000/month",
            "description": "🚂 Power India's lifeline! Learn operations of world's largest railway network."
        },
        {
            "company": "CSIR Labs",
            "title": "Scientific Research Intern",
            "type": "government",
            "sector": "Scientific Research",
            "skills": ["Research", "Data Analysis", "Laboratory Skills", "Scientific Writing", "Innovation"],
            "duration": "6 Months",
            "location": "Multiple CSIR Centers",
            "stipend": "₹24,000/month",
            "description": "🔬 Advance scientific knowledge! Work with India's premier scientific research organization."
        },
        {
            "company": "Ministry of Electronics & IT",
            "title": "Digital India Technology Intern",
            "type": "government",
            "sector": "Digital Governance",
            "skills": ["Programming", "Digital Literacy", "Web Development", "Data Management", "Cybersecurity"],
            "duration": "4 Months",
            "location": "New Delhi/Pune",
            "stipend": "₹21,000/month",
            "description": "💻 Build Digital India! Contribute to nation's digital transformation and e-governance initiatives."
        },
        {
            "company": "BARC",
            "title": "Nuclear Technology Research Intern",
            "type": "government",
            "sector": "Nuclear Research",
            "skills": ["Engineering", "Research", "Data Analysis", "Safety Protocols", "Technical Documentation"],
            "duration": "5 Months",
            "location": "Mumbai/Kalpakkam",
            "stipend": "₹26,000/month",
            "description": "⚛️ Power India's future! Work on nuclear technology and contribute to clean energy research."
        },

        # PRIVATE-BASED INTERNSHIPS (8 options - high quality with competitive stipends)
        {
            "company": "TCS (Tata Consultancy Services)",
            "title": "Software Development Intern",
            "type": "private-based",
            "sector": "IT Services",
            "skills": ["Java", "Python", "Programming", "Problem Solving", "Communication"],
            "duration": "3 Months",
            "location": "Multiple Cities",
            "stipend": "₹30,000/month",
            "description": "💼 Industry leader experience! Work on enterprise software projects with India's largest IT company."
        },
        {
            "company": "Infosys",
            "title": "Digital Innovation Intern",
            "type": "private-based",
            "sector": "IT Consulting",
            "skills": ["Digital Technologies", "Innovation", "Cloud Computing", "Problem Solving", "Teamwork"],
            "duration": "3 Months",
            "location": "Bangalore/Pune",
            "stipend": "₹28,000/month",
            "description": "🌟 Innovation at scale! Work on cutting-edge digital transformation projects with global impact."
        },
        {
            "company": "Wipro",
            "title": "Technology Solutions Intern",
            "type": "private-based",
            "sector": "IT Services",
            "skills": ["Cloud Computing", "DevOps", "Programming", "Agile", "Learning Agility"],
            "duration": "4 Months",
            "location": "Pune/Bangalore",
            "stipend": "₹32,000/month",
            "description": "☁️ Future-ready skills! Gain hands-on experience with cloud technologies and modern development practices."
        },
        {
            "company": "Microsoft India",
            "title": "Technology Trainee",
            "type": "private-based",
            "sector": "Technology",
            "skills": ["Programming", "AI/ML", "Cloud Platforms", "Data Science", "Innovation"],
            "duration": "3 Months",
            "location": "Hyderabad/Bangalore",
            "stipend": "₹40,000/month",
            "description": "🚀 Global technology experience! Work with cutting-edge Microsoft technologies and AI platforms."
        },
        {
            "company": "Google India",
            "title": "Software Engineering Intern",
            "type": "private-based",
            "sector": "Technology",
            "skills": ["Programming", "Algorithms", "Data Structures", "Problem Solving", "Software Design"],
            "duration": "4 Months",
            "location": "Bangalore/Gurgaon",
            "stipend": "₹50,000/month",
            "description": "🌟 Dream opportunity! Work with world-class engineers on products used by billions."
        },
        {
            "company": "Amazon India",
            "title": "SDE Intern",
            "type": "private-based",
            "sector": "E-commerce Technology",
            "skills": ["Programming", "System Design", "AWS", "Data Structures", "Problem Solving"],
            "duration": "3 Months",
            "location": "Bangalore/Hyderabad",
            "stipend": "₹45,000/month",
            "description": "📦 Scale at Amazon! Work on systems handling millions of customers and learn cloud technologies."
        },
        {
            "company": "HDFC Bank",
            "title": "Banking Technology Intern",
            "type": "private-based",
            "sector": "Financial Services",
            "skills": ["Financial Technology", "Data Analysis", "Banking Operations", "Communication", "Excel"],
            "duration": "3 Months",
            "location": "Mumbai/Pune",
            "stipend": "₹25,000/month",
            "description": "🏦 FinTech innovation! Experience digital banking transformation with India's leading private bank."
        },
        {
            "company": "Accenture",
            "title": "Technology Consulting Intern",
            "type": "private-based",
            "sector": "IT Consulting",
            "skills": ["Business Analysis", "Technology Consulting", "Communication", "Problem Solving", "Project Management"],
            "duration": "4 Months",
            "location": "Multiple Cities",
            "stipend": "₹27,000/month",
            "description": "💡 Consulting excellence! Work with global clients on technology transformation projects."
        }
    ]

    # Return balanced top 5 with government priority
    return sort_recommendations_by_match(all_recommendations, user)

# 🔧 ENHANCED: Better error handling and timeout for AI recommendations
def generate_recommendations_fast(user):
    """Fast AI recommendations with enhanced error handling and fallback"""
    try:
        model_instance = get_gemini_model()
        if not model_instance:
            print("📋 Using enhanced default recommendations (Gemini not available)")
            return get_enhanced_default_recommendations(user)
            
        # Shorter, more focused prompt for faster response
        user_skills = user.get('skills', 'General')
        if isinstance(user_skills, list):
            skills_str = ', '.join(user_skills)
        else:
            skills_str = str(user_skills)
            
        prompt = f"""
        Generate 6 internship recommendations for:
        - Skills: {skills_str}
        - Interest: {user.get('area_of_interest', 'IT')}
        - Education: {user.get('qualification', 'Graduate')}

        IMPORTANT: Include more government internships (ISRO, DRDO, NITI Aayog, etc.)

        JSON format: [{{"company":"Name","title":"Position","type":"government|private-based","sector":"Sector","skills":["skill1","skill2"],"duration":"X Months","location":"City","stipend":"₹X/month","description":"Brief desc"}}]
        """

        # 🔧 ENHANCED: Better timeout and error handling
        try:
            response = model_instance.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=1000,  # Increased for better responses
                    temperature=0.7,
                )
            )
            
            if not response or not response.text:
                raise Exception("Empty response from Gemini")
                
            recommendations_text = response.text.strip()
            start_idx = recommendations_text.find('[')
            end_idx = recommendations_text.rfind(']') + 1
            
            if start_idx != -1 and end_idx != -1:
                json_str = recommendations_text[start_idx:end_idx]
                recommendations = json.loads(json_str)
                print(f"✅ AI generated {len(recommendations)} recommendations")
                return sort_recommendations_by_match(recommendations[:6], user)
            else:
                print("⚠️ Could not parse AI response format, using fallback")
                raise Exception("Could not parse AI response")
                
        except json.JSONDecodeError as json_error:
            print(f"🔄 JSON parsing failed: {json_error}")
            raise json_error
            
        except Exception as api_error:
            print(f"🔄 Gemini API call failed: {api_error}")
            raise api_error
            
    except Exception as e:
        print(f"📋 AI recommendation error: {e}")
        print("🔄 Using enhanced default recommendations")
        return get_enhanced_default_recommendations(user)

def get_default_recommendations(user):
    """Legacy function - calls enhanced version"""
    return get_enhanced_default_recommendations(user)

# Login required decorator
def login_required(view_function):
    @wraps(view_function)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Please log in to access this page', 'error')
            return redirect(url_for('login'))
        return view_function(*args, **kwargs)
    return decorated_function


@app.before_request
def ensure_language_selection():
    """Guarantee the session language is always set to a supported option."""
    language = session.get('language')
    if not language or language not in SUPPORTED_LANGUAGES:
        session['language'] = DEFAULT_LANGUAGE


@app.route('/language/<lang_code>')
def change_language(lang_code):
    """Persist the requested language in the session and redirect back."""
    lang_code = (lang_code or '').lower()
    if lang_code in SUPPORTED_LANGUAGES:
        session['language'] = lang_code
        print(f"🌐 Language changed to: {lang_code}")
    else:
        flash('Selected language is not supported yet.', 'info')

    next_url = request.referrer
    if not next_url:
        next_url = url_for('home') if session.get('logged_in') else url_for('index')
    return redirect(next_url)


@app.context_processor
def inject_translation_helpers():
    """Expose translation helpers to templates."""
    current_language = session.get('language', DEFAULT_LANGUAGE)

    def t(key, lang=None):
        return get_translation(key, lang or current_language)

    return {
        't': t,
        'current_language': current_language,
        'languages': SUPPORTED_LANGUAGES
    }

@app.before_request
def clear_stale_flash_messages():
    """Clear flash messages for non-authenticated users"""
    if request.endpoint not in ['login', 'signup', 'logout', 'clear_session', 'index'] and not session.get('logged_in'):
        if '_flashes' in session:
            session.pop('_flashes', None)

@app.context_processor
def inject_user():
    """Inject user data into all templates"""
    user = None
    if session.get('logged_in') and session.get('user_id'):
        user = get_user_by_id(session.get('user_id'))
    
    return {
        'user': user,
        'user_name': session.get('user_name', 'User'),
        'user_email': session.get('user_email', ''),
        'user_initials': session.get('user_initials', 'U')
    }

# Routes
@app.route('/')
def index():
    if session.get('logged_in'):
        return redirect(url_for('home'))
    else:
        # Redirect to login page if not logged in
        return redirect(url_for('login'))

# 🔧 FIXED: Home route with better profile completion check and debug logging
@app.route('/home')
@login_required
def home():
    user = get_user_by_id(session.get('user_id'))
    if not user:
        flash('User session expired. Please log in again.', 'error')
        return redirect(url_for('login'))
    
    # 🔧 FIXED: Add debug logging and improved profile completion check
    print(f"🔍 DEBUG: User {user['id']} accessing home")
    print(f"🔍 DEBUG: profile_completed = {user.get('profile_completed')}")
    print(f"🔍 DEBUG: registration_completed = {user.get('registration_completed')}")
    print(f"🔍 DEBUG: full_name = {user.get('full_name')}")
    print(f"🔍 DEBUG: phone = {user.get('phone')}")
    
    # 🔧 IMPROVED: More flexible profile completion check
    # Consider profile complete if user has basic info filled OR profile_completed flag is True
    has_basic_info = (
        user.get('full_name') and user.get('full_name') != 'User' and 
        user.get('phone') and len(str(user.get('phone', ''))) >= 10
    )
    
    profile_complete = user.get('profile_completed') == True or has_basic_info
    
    print(f"🔍 DEBUG: has_basic_info = {has_basic_info}")
    print(f"🔍 DEBUG: final profile_complete = {profile_complete}")
    
    if not profile_complete:
        flash('Please complete your profile first to access all features', 'info')
        return redirect(url_for('profile'))
    
    return render_template('home.html')

@app.route('/ats')
@login_required
def ats():
    user = get_user_by_id(session.get('user_id'))
    if not user:
        return redirect(url_for('login'))
    
    if not user.get('profile_completed'):
        flash('Please complete your profile first to use ATS matching', 'info')
        return redirect(url_for('profile'))
    
    return render_template('ats.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember')
        captcha_answer = request.form.get('captcha', '')
        
        # Clear any existing flash messages
        session.pop('_flashes', None)
        
        # Basic validation
        if not email or not password:
            flash('📝 Please enter both email and password', 'error')
            # Generate new captcha for the form
            captcha_question, captcha_answer_correct = generate_captcha()
            session['captcha_answer'] = captcha_answer_correct
            return render_template('login.html', captcha_question=captcha_question)
        
        # Captcha verification
        if not captcha_answer:
            flash('🔒 Please solve the captcha', 'error')
            captcha_question, captcha_answer_correct = generate_captcha()
            session['captcha_answer'] = captcha_answer_correct
            return render_template('login.html', captcha_question=captcha_question)
        
        if not verify_captcha(captcha_answer, session.get('captcha_answer')):
            flash('❌ Incorrect captcha. Please try again.', 'error')
            captcha_question, captcha_answer_correct = generate_captcha()
            session['captcha_answer'] = captcha_answer_correct
            return render_template('login.html', captcha_question=captcha_question)
        
        # Email format validation
        if not validate_email(email):
            flash('📧 Please enter a valid email address', 'error')
            return render_template('login.html')
        
        # Verify user credentials
        user = verify_user(email, password)
        
        if user:
            # Login successful - use the helper function
            full_name = setup_user_session(user, remember)
            
            flash(f'🎉 Welcome back, {full_name}!', 'success')
            
            # Redirect based on profile completion
            if user.get('profile_completed'):
                return redirect(url_for('home'))
            else:
                return redirect(url_for('profile'))
        else:
            # Login failed - check specific reason
            if check_email_exists(email):
                flash('❌ Incorrect password. Please check your password and try again.', 'error')
            else:
                flash('❌ No account found with this email address.', 'error')
                flash('💡 Don\'t have an account? Sign up to get started!', 'info')
            # Generate new captcha for retry
            captcha_question, captcha_answer_correct = generate_captcha()
            session['captcha_answer'] = captcha_answer_correct
            return render_template('login.html', captcha_question=captcha_question)
    
    # GET request - generate captcha
    captcha_question, captcha_answer_correct = generate_captcha()
    session['captcha_answer'] = captcha_answer_correct
    return render_template('login.html', captcha_question=captcha_question)

# 🔧 ENHANCED: Signup route with auto-login after successful account creation
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        captcha_answer = request.form.get('captcha', '')
        
        session.pop('_flashes', None)
        
        # Validation
        if not full_name or not email or not password or not confirm_password:
            flash('All fields are required', 'error')
            # Generate new captcha for the form
            captcha_question, captcha_answer_correct = generate_captcha()
            session['captcha_answer'] = captcha_answer_correct
            return render_template('signup.html', captcha_question=captcha_question)
        
        # Captcha verification
        if not captcha_answer:
            flash('🔒 Please solve the captcha', 'error')
            captcha_question, captcha_answer_correct = generate_captcha()
            session['captcha_answer'] = captcha_answer_correct
            return render_template('signup.html', captcha_question=captcha_question)
        
        if not verify_captcha(captcha_answer, session.get('captcha_answer')):
            flash('❌ Incorrect captcha. Please try again.', 'error')
            captcha_question, captcha_answer_correct = generate_captcha()
            session['captcha_answer'] = captcha_answer_correct
            return render_template('signup.html', captcha_question=captcha_question)
        
        if len(full_name.strip()) < 2:
            flash('Full name must be at least 2 characters long', 'error')
            captcha_question, captcha_answer_correct = generate_captcha()
            session['captcha_answer'] = captcha_answer_correct
            return render_template('signup.html', captcha_question=captcha_question)
        
        if not validate_email(email):
            flash('Please enter a valid email address', 'error')
            captcha_question, captcha_answer_correct = generate_captcha()
            session['captcha_answer'] = captcha_answer_correct
            return render_template('signup.html', captcha_question=captcha_question)
        
        if check_email_exists(email):
            flash('This email is already registered. Please use a different email or try logging in.', 'error')
            captcha_question, captcha_answer_correct = generate_captcha()
            session['captcha_answer'] = captcha_answer_correct
            return render_template('signup.html', captcha_question=captcha_question)
        
        if password != confirm_password:
            flash('Passwords do not match', 'error')
            captcha_question, captcha_answer_correct = generate_captcha()
            session['captcha_answer'] = captcha_answer_correct
            return render_template('signup.html', captcha_question=captcha_question)
        
        is_valid, message = validate_password(password)
        if not is_valid:
            flash(message, 'error')
            return render_template('signup.html')
        
        # 🔧 ENHANCED: Create user and get user data for auto-login
        success, message, created_user = create_user(full_name, email, password)
        
        if success and created_user:
            # 🎉 AUTO-LOGIN: Set up user session immediately after successful signup
            full_name = setup_user_session(created_user, remember=True)  # Auto-remember for convenience
            
            # Enhanced success message with auto-login confirmation
            flash(f'🎉 Welcome {full_name}! Your account has been created and you are now logged in!', 'success')
            flash('Please complete your profile to access all features.', 'info')
            
            print(f"✅ Auto-login successful for new user: {created_user['id']}")
            
            # 🔧 DIRECT REDIRECT: Go straight to profile page to complete setup
            return redirect(url_for('profile'))
        else:
            flash(message, 'error')
            # Generate new captcha for retry
            captcha_question, captcha_answer_correct = generate_captcha()
            session['captcha_answer'] = captcha_answer_correct
            return render_template('signup.html', captcha_question=captcha_question)
    
    # GET request - generate captcha
    captcha_question, captcha_answer_correct = generate_captcha()
    session['captcha_answer'] = captcha_answer_correct
    return render_template('signup.html', captcha_question=captcha_question)

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    """Handle user logout with proper session cleanup"""
    try:
        # Get user info before clearing session (for logging)
        user_id = session.get('user_id')
        username = session.get('username', 'Unknown')
        
        # Clear all session data
        session.clear()
        
        # Log successful logout
        if user_id:
            print(f"✅ User {username} (ID: {user_id}) logged out successfully")
        else:
            print("✅ Session cleared (no active user found)")
        
        # Set success message
        flash('You have been logged out successfully', 'success')
        
        # Create response with redirect to login page
        response = make_response(redirect(url_for('login')))
        
        # Clear any additional cookies if needed
        response.set_cookie('session', '', expires=0)
        
        return response
        
    except Exception as e:
        print(f"❌ Logout error: {e}")
        # Even if there's an error, still try to clear session and redirect
        session.clear()
        flash('Logged out', 'info')
        return redirect(url_for('login'))

# Alternative logout route for testing
@app.route('/force-logout')
def force_logout():
    """Force logout - for testing purposes"""
    session.clear()
    flash('Force logout completed', 'info')
    return redirect(url_for('login'))

@app.route('/api/save_profile', methods=['POST'])
@login_required
def save_profile():
    try:
        form_data = request.get_json()
        
        # Convert numeric fields
        if 'qualification_marks' in form_data:
            try:
                form_data['qualification_marks'] = float(form_data['qualification_marks'])
            except (TypeError, ValueError):
                form_data['qualification_marks'] = None
        
        if 'course_marks' in form_data:
            try:
                form_data['course_marks'] = float(form_data['course_marks'])
            except (TypeError, ValueError):
                form_data['course_marks'] = None
        
        # Add profile completion flags
        form_data.update({
            'otp_verified': True,
            'registration_completed': True,
            'profile_completed': True
        })
        
        # Handle file paths (for future file upload support)
        file_paths = {}
        
        if update_user_profile(session.get('user_id'), {**form_data, **file_paths}):
            if form_data.get('full_name'):
                session['user_name'] = form_data['full_name']
                session['user_initials'] = get_user_initials(form_data['full_name'])
            
            return jsonify({'success': True, 'message': 'Profile updated successfully!'})
        else:
            return jsonify({'success': False, 'error': 'Failed to update profile'}), 500
            
    except Exception as e:
        print(f"Profile update error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# 🔧 FIXED: Profile route with separate career objective and area of interest
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = get_user_by_id(session.get('user_id'))
    if not user:
        flash('User not found', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        try:
            # Handle file uploads
            uploaded_files = {}
            file_fields = ['qualificationCertificate', 'additionalCertificates', 'internshipCertificate']
            
            for field_name in file_fields:
                if field_name in request.files:
                    files = request.files.getlist(field_name)
                    saved_files = []
                    for file in files:
                        if file and file.filename and allowed_file(file.filename):
                            filename = secure_filename(file.filename)
                            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
                            filename = timestamp + filename
                            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                            try:
                                file.save(file_path)
                                saved_files.append(filename)
                            except Exception as e:
                                print(f"File save error: {e}")
                    
                    if saved_files:
                        db_field_map = {
                            'qualificationCertificate': 'qualification_certificate',
                            'additionalCertificates': 'additional_certificates',
                            'internshipCertificate': 'internship_certificate'
                        }
                        db_field = db_field_map.get(field_name, field_name)
                        uploaded_files[db_field] = json.dumps(saved_files)

            # Collect skills from checkboxes - UPDATED with new skills
            skills_list = []
            skill_fields = ['react', 'python', 'java', 'cpp', 'html', 'css', 'javascript', 'ai-ml', 'cloud', 
                           'nodejs', 'database', 'devops',  # New technical skills
                           'leadership', 'communication', 'digital-marketing', 'content-writing', 'project-management',
                           'teamwork', 'problem-solving', 'analytical']  # New non-technical skills
            
            for skill in skill_fields:
                if request.form.get(f'skill_{skill}') or skill in request.form.getlist('skills'):
                    skills_list.append(skill)
            
            # Collect languages from checkboxes
            languages_list = []
            language_fields = ['english', 'hindi', 'tamil', 'telugu', 'bengali', 'kannada', 'marathi', 'other']
            
            for lang in language_fields:
                if request.form.get(f'lang_{lang}') or lang in request.form.getlist('languages'):
                    languages_list.append(lang)

            # 🔧 FIXED: Handle Career Objective and Area of Interest SEPARATELY
            # Career Objective = user's typed content in textarea (objective field)
            # Area of Interest = dropdown selection (interest field)

            career_objective = request.form.get('objective', '').strip()  # User's typed career objective
            area_interest = request.form.get('interest', '').strip()      # Dropdown selection for area of interest

            # If user hasn't selected area of interest dropdown, keep existing value
            if not area_interest and user:
                area_interest = user.get('area_of_interest', '')

            print(f"🔍 DEBUG: career_objective (user typed) = '{career_objective}'")
            print(f"🔍 DEBUG: area_interest (dropdown) = '{area_interest}'")

            # Process form data matching your database schema
            form_data = {
                'full_name': request.form.get('fullName', '').strip(),
                'father_name': request.form.get('fatherName', '').strip(),
                'gender': request.form.get('gender', ''),
                'phone': request.form.get('phone', '').strip(),
                'district': request.form.get('district', ''),
                'address': request.form.get('address', '').strip(),
                'career_objective': career_objective,  # 🔧 NEW: Store user's career objective separately
                'area_of_interest': area_interest,     # 🔧 SEPARATE: Store dropdown selection
                'qualification': request.form.get('qualification', ''),
                'qualification_marks': float(request.form.get('qualificationMarks', 0)) if request.form.get('qualificationMarks') else None,
                'course': request.form.get('course', '').strip(),
                'course_marks': float(request.form.get('courseMarks', 0)) if request.form.get('courseMarks') else None,
                'skills': json.dumps(skills_list) if skills_list else json.dumps([]),
                'languages': json.dumps(languages_list) if languages_list else json.dumps([]),
                'experience': request.form.get('experience', ''),
                'prior_internship': request.form.get('priorInternship', '')
            }
            
            # Add file upload data
            form_data.update(uploaded_files)
            
            # 🔧 CRITICAL FIX: Update user profile and ensure success
            if update_user_profile(user['id'], form_data):
                # Update session with new name
                if form_data.get('full_name'):
                    session['user_name'] = form_data['full_name']
                    session['user_initials'] = get_user_initials(form_data['full_name'])
                
                flash('Profile saved successfully! 🎉', 'success')
                
                # 🔧 FIXED: Redirect to home page after successful profile save
                print(f"🔍 DEBUG: Profile saved successfully, redirecting to home")
                return redirect(url_for('home'))
            else:
                flash('Failed to update profile. Please try again.', 'error')
                
        except Exception as e:
            print(f"Profile update error: {e}")
            flash('Error updating profile. Please try again.', 'error')
    
    # For GET request or after failed POST, return form with user data
    return render_template('profile.html', user=user)

# ENHANCED: Recommendations route with balanced top 5 results and government preference
@app.route('/recommendations')
@login_required
def recommendations():
    user = get_user_by_id(session.get('user_id'))
    if not user:
        return redirect(url_for('login'))
    
    # Check if profile is completed
    if not user.get('profile_completed'):
        flash('Please complete your profile first to get personalized recommendations.', 'warning')
        return redirect(url_for('profile'))
    
    # Get top 5 balanced recommendations with government priority
    top_recommendations = get_enhanced_default_recommendations(user)
    
    return render_template('recommendations.html',
                         user=user,
                         recommendations=top_recommendations)

# ENHANCED: AI recommendations with skill matching and government preference
@app.route('/api/generate-ai-recommendations')
@login_required
def generate_ai_recommendations():
    """AJAX endpoint to generate AI recommendations sorted by match score with government preference"""
    user = get_user_by_id(session.get('user_id'))
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    try:
        # Try to generate AI recommendations
        ai_recommendations = generate_recommendations_fast(user)
        
        # Sort AI recommendations by skill match with government preference
        sorted_recommendations = sort_recommendations_by_match(ai_recommendations, user)
        
        return jsonify({
            'success': True,
            'recommendations': sorted_recommendations
        })
        
    except Exception as e:
        print(f"AI recommendations error: {e}")
        
        # Fallback to enhanced default recommendations
        fallback_recommendations = get_enhanced_default_recommendations(user)
        
        return jsonify({
            'success': True,
            'recommendations': fallback_recommendations
        })

@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        user_message = data.get('message', '').strip()
        
        if not user_message:
            return jsonify({
                'error': 'No message provided',
                'reply': '🤔 I didn\'t receive your message. Please try typing something!',
                'success': False
            }), 400
        
        # Message length validation
        if len(user_message) > 500:
            return jsonify({
                'error': 'Message too long',
                'reply': '📝 Please keep your message under 500 characters for better assistance!',
                'success': False
            }), 400
        
        user_name = session.get('user_name', 'User')
        user_email = session.get('user_email', '')
        
        # Get enhanced response
        bot_response = get_gemini_response(user_message, user_name, user_email)
        
        # Log conversation for analytics
        log_conversation(user_message, bot_response, session.get('user_id'))
        
        return jsonify({
            'reply': bot_response,
            'success': True,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        print(f"Chat error: {e}")
        
        # Enhanced error responses based on error type
        if "quota" in str(e).lower() or "limit" in str(e).lower():
            error_response = "🚫 I'm experiencing high traffic right now. Please try again in a few moments!"
        elif "network" in str(e).lower() or "connection" in str(e).lower():
            error_response = "🌐 Connection issue detected. Please check your internet and try again."
        else:
            error_response = "⚠️ I encountered a technical issue. Let me try a different approach!"
        
        # Fall back to intelligent response
        fallback_response = get_fallback_response(data.get('message', ''))
        
        return jsonify({
            'reply': fallback_response,
            'success': True,
            'fallback': True,
            'timestamp': datetime.now().isoformat()
        }), 200

# New endpoint to clear chat history
@app.route('/chat/clear', methods=['POST'])
def clear_chat_history():
    try:
        session.pop('chat_history', None)
        return jsonify({
            'success': True,
            'message': 'Chat history cleared successfully'
        })
    except Exception as e:
        print(f"Clear chat error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to clear chat history'
        }), 500

@app.route('/clear-session')
def clear_session():
    session.clear()
    return redirect(url_for('index'))

# 🔧 ADDED: Debug route to check profile status
@app.route('/debug-profile')
@login_required
def debug_profile():
    """Debug route to check profile status"""
    if not app.debug:
        return "Not available in production"
    
    user = get_user_by_id(session.get('user_id'))
    if not user:
        return "User not found"
    
    return f"""
    <h2>Profile Debug Info</h2>
    <p><strong>User ID:</strong> {user['id']}</p>
    <p><strong>Full Name:</strong> {user.get('full_name')}</p>
    <p><strong>Profile Completed:</strong> {user.get('profile_completed')}</p>
    <p><strong>Registration Completed:</strong> {user.get('registration_completed')}</p>
    <p><strong>Phone:</strong> {user.get('phone')}</p>
    <p><strong>Career Objective:</strong> {user.get('career_objective')}</p>
    <p><strong>Area of Interest:</strong> {user.get('area_of_interest')}</p>
    <p><strong>Skills:</strong> {user.get('skills')}</p>
    <p><strong>Updated At:</strong> {user.get('updated_at')}</p>
    <br>
    <a href="/home">Try Home Page</a><br>
    <a href="/profile">Back to Profile</a><br>
    <a href="/logout">Logout</a>
    """

# Debug routes (remove in production)
@app.route('/debug-users')
def debug_users():
    """Debug route to see all users"""
    if not app.debug:
        return "Not available in production"
    
    try:
        if not supabase:
            return "Database connection not available"
        
        response = supabase.table('users').select('id, full_name, email, profile_completed, created_at').execute()
        users = response.data
        
        output = "<h2>Users in Database:</h2>"
        for user in users:
            output += f"<p>ID: {user['id']}, Name: {user['full_name']}, Email: {user['email']}, Completed: {user.get('profile_completed', False)}</p>"
        
        return output
        
    except Exception as e:
        return f"Error: {e}"


def generate_cv_pdf(user):
    """Generate a professional CV PDF from user profile data"""
    try:
        buffer = io.BytesIO()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=40,
            leftMargin=40,
            topMargin=60,
            bottomMargin=40
        )

        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#2c3e50'),
            spaceAfter=6,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        )

        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Normal'],
            fontSize=12,
            textColor=colors.HexColor('#7f8c8d'),
            spaceAfter=20,
            alignment=TA_CENTER,
            fontName='Helvetica'
        )

        section_style = ParagraphStyle(
            'SectionHeader',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#3498db'),
            spaceBefore=15,
            spaceAfter=8,
            fontName='Helvetica-Bold',
            borderWidth=1,
            borderColor=colors.HexColor('#3498db'),
            borderPadding=5
        )

        content_style = ParagraphStyle(
            'Content',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#2c3e50'),
            spaceAfter=6,
            fontName='Helvetica'
        )

        story = []

        full_name = user.get('full_name', 'No Name Provided')
        story.append(Paragraph(full_name.upper(), title_style))

        contact_info = []
        if user.get('email'):
            contact_info.append(f"📧 {user['email']}")
        if user.get('phone'):
            contact_info.append(f"📱 {user['phone']}")
        if user.get('district'):
            contact_info.append(f"📍 {user['district'].title()}")

        if contact_info:
            story.append(Paragraph(" | ".join(contact_info), subtitle_style))

        story.append(Spacer(1, 0.2*inch))

        story.append(Paragraph("PERSONAL INFORMATION", section_style))

        personal_data = []
        if user.get('father_name'):
            personal_data.append(["Father's Name:", user['father_name']])
        if user.get('gender'):
            personal_data.append(['Gender:', user['gender'].title()])
        if user.get('address'):
            personal_data.append(['Address:', user['address']])

        if personal_data:
            personal_table = Table(personal_data, colWidths=[2*inch, 4*inch])
            personal_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#3498db')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]))
            story.append(personal_table)
        else:
            story.append(Paragraph("Personal information not provided", content_style))

        story.append(Spacer(1, 0.2*inch))

        if user.get('career_objective'):
            story.append(Paragraph("CAREER OBJECTIVE", section_style))
            story.append(Paragraph(user['career_objective'], content_style))
            story.append(Spacer(1, 0.1*inch))

        story.append(Paragraph("EDUCATION", section_style))

        education_data = []
        if user.get('qualification'):
            education_data.append(['Qualification:', user['qualification'].upper()])
        if user.get('qualification_marks'):
            education_data.append(['Marks:', f"{user['qualification_marks']}%"])
        if user.get('course'):
            education_data.append(['Course:', user['course']])
        if user.get('course_marks'):
            education_data.append(['Course Marks:', f"{user['course_marks']}%"])

        if education_data:
            education_table = Table(education_data, colWidths=[2*inch, 4*inch])
            education_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#3498db')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]))
            story.append(education_table)

        story.append(Spacer(1, 0.1*inch))

        if user.get('skills'):
            story.append(Paragraph("TECHNICAL SKILLS", section_style))

            skills = user['skills']
            if isinstance(skills, list):
                skills_text = ", ".join([skill.title() for skill in skills])
            else:
                try:
                    skills_list = json.loads(skills)
                    skills_text = ", ".join([skill.title() for skill in skills_list])
                except:
                    skills_text = str(skills).replace(',', ', ').title()

            story.append(Paragraph(skills_text, content_style))
            story.append(Spacer(1, 0.1*inch))

        if user.get('languages'):
            story.append(Paragraph("LANGUAGES", section_style))

            languages = user['languages']
            if isinstance(languages, list):
                languages_text = ", ".join([lang.title() for lang in languages])
            else:
                try:
                    languages_list = json.loads(languages)
                    languages_text = ", ".join([lang.title() for lang in languages_list])
                except:
                    languages_text = str(languages).replace(',', ', ').title()

            story.append(Paragraph(languages_text, content_style))
            story.append(Spacer(1, 0.1*inch))

        if user.get('experience'):
            story.append(Paragraph("EXPERIENCE LEVEL", section_style))
            experience_text = user['experience'].replace('-', ' - ').replace('_', ' ').title()
            story.append(Paragraph(experience_text, content_style))
            story.append(Spacer(1, 0.1*inch))

        if user.get('area_of_interest'):
            story.append(Paragraph("AREA OF INTEREST", section_style))
            interest_text = user['area_of_interest'].replace('-', ' ').replace('_', ' ').title()
            story.append(Paragraph(interest_text, content_style))
            story.append(Spacer(1, 0.1*inch))

        if user.get('prior_internship') == 'yes':
            story.append(Paragraph("INTERNSHIP EXPERIENCE", section_style))
            story.append(Paragraph("Previous internship experience completed", content_style))
            story.append(Spacer(1, 0.1*inch))

        story.append(Spacer(1, 0.3*inch))
        footer_style = ParagraphStyle(
            'Footer',
            parent=styles['Normal'],
            fontSize=8,
            textColor=colors.HexColor('#7f8c8d'),
            alignment=TA_CENTER,
            fontName='Helvetica-Oblique'
        )
        story.append(Paragraph("Generated from PM Internship Scheme Profile", footer_style))

        doc.build(story)

        pdf_data = buffer.getvalue()
        buffer.close()

        return pdf_data

    except Exception as e:
        print(f"Error generating CV PDF: {e}")
        return None


def get_cv_filename(user):
    """Generate a clean filename for the CV"""
    name = user.get('full_name', 'User')
    clean_name = re.sub(r'[^\w\s-]', '', name)
    clean_name = re.sub(r'[-\s]+', '_', clean_name)
    return f"{clean_name}_CV.pdf"

@app.route('/preview-cv')
@login_required
def preview_cv():
    """Preview user's professional CV in browser"""
    user = get_user_by_id(session.get('user_id'))
    if not user:
        flash('User not found', 'error')
        return redirect(url_for('login'))
    
    if not user.get('profile_completed'):
        flash('Please complete your profile first to preview your CV', 'warning')
        return redirect(url_for('profile'))
    
    try:
        print(f"🔍 DEBUG: Starting CV generation for user: {user.get('full_name', 'Unknown')}")
        
        # FIXED: Check if generate_cv_pdf function exists and is callable
        if 'generate_cv_pdf' not in globals():
            print("❌ ERROR: generate_cv_pdf function not found!")
            flash('CV generation function not available. Please contact support.', 'error')
            return redirect(url_for('profile'))
        
        # Generate the PDF binary data
        pdf_data = generate_cv_pdf(user)
        print(f"🔍 DEBUG: PDF generation returned data of type: {type(pdf_data)}")
        
        if pdf_data and len(pdf_data) > 0:
            print(f"✅ PDF generated successfully, size: {len(pdf_data)} bytes")
            
            # FIXED: Create response for inline viewing with actual PDF data
            response = make_response(pdf_data)
            response.headers['Content-Type'] = 'application/pdf'
            response.headers['Content-Disposition'] = 'inline'
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            response.headers['Accept-Ranges'] = 'bytes'
            response.headers['Content-Length'] = len(pdf_data)
            
            print("✅ CV preview response created successfully")
            return response
        else:
            print("❌ PDF generation returned empty or None data")
            flash('Error generating CV preview. Please try again.', 'error')
            return redirect(url_for('profile'))
            
    except NameError as ne:
        print(f"❌ NameError in CV preview: {ne}")
        if 'make_response' in str(ne):
            flash('CV preview functionality unavailable due to missing dependencies.', 'error')
        elif 'generate_cv_pdf' in str(ne):
            flash('CV generation function not found. Please contact support.', 'error')
        else:
            flash(f'CV preview error: {ne}', 'error')
        return redirect(url_for('profile'))
        
    except Exception as e:
        print(f"❌ CV preview error: {e}")
        import traceback
        traceback.print_exc()
        flash('Error previewing CV. Please try again.', 'error')
        return redirect(url_for('profile'))

# FIXED: Also fix the download-cv route if you have it
@app.route('/download-cv')
@login_required
def download_cv():
    """Generate and download user's professional CV as PDF"""
    user = get_user_by_id(session.get('user_id'))
    if not user:
        flash('User not found', 'error')
        return redirect(url_for('login'))
    
    if not user.get('profile_completed'):
        flash('Please complete your profile first to download your CV', 'warning')
        return redirect(url_for('profile'))
    
    try:
        # Generate the PDF binary data
        pdf_data = generate_cv_pdf(user)
        
        if pdf_data and len(pdf_data) > 0:
            # Get professional filename (you may need to implement this)
            filename = f"CV_{user.get('full_name', 'User').replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
            
            # Create response for download
            response = make_response(pdf_data)
            response.headers['Content-Type'] = 'application/pdf'
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
            response.headers['Content-Length'] = len(pdf_data)
            response.headers['Cache-Control'] = 'no-cache'
            
            print(f"✅ Professional CV downloaded successfully for user: {user['full_name']}")
            return response
        else:
            flash('Error generating CV. Please try again.', 'error')
            return redirect(url_for('profile'))
            
    except Exception as e:
        print(f"CV download error: {e}")
        flash('Error downloading CV. Please try again.', 'error')
        return redirect(url_for('profile'))

from ats import ProfessionalATSAnalyzer

# Initialize the analyzer
ats_analyzer = ProfessionalATSAnalyzer()

@app.route('/analyze-cv', methods=['POST'])
@login_required
def analyze_cv():
    """Analyze uploaded CV against job description"""
    try:
        user = get_user_by_id(session.get('user_id'))
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # Get uploaded file and job description
        if 'cv_file' not in request.files:
            return jsonify({'error': 'No CV file uploaded'}), 400
        
        cv_file = request.files['cv_file']
        job_description = request.form.get('job_description', '')
        
        if not cv_file.filename:
            return jsonify({'error': 'No file selected'}), 400
        
        # Save uploaded file temporarily
        filename = secure_filename(cv_file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{filename}")
        cv_file.save(file_path)
        
        # Analyze the CV
        analysis_result = ats_analyzer.calculate_comprehensive_ats_score(
            file_path, 
            job_description, 
            user_profile=user
        )
        
        # Clean up temporary file
        os.remove(file_path)
        
        return jsonify({
            'success': True,
            'analysis': analysis_result
        })
        
    except Exception as e:
        print(f"CV analysis error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to analyze CV. Please try again.'
        }), 500


if __name__ == '__main__':
    # For local development
    app.run(debug=True, host='0.0.0.0', port=5000)

# For Vercel deployment
app = app
