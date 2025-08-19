# Restaurant Voice Agent

A Python-based voice agent for restaurant operations using Deepgram Voice Agent API and Twilio.

## Setup for Render Deployment

1. **Environment Variables** (set in Render):
   - `DEEPGRAM_API_KEY` - Your Deepgram API key
   - `SUPABASE_URL` - Your Supabase project URL
   - `SUPABASE_SERVICE_ROLE_KEY` - Your Supabase service role key

2. **Render Configuration**:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
   - **Environment**: Python 3.9+

3. **Twilio Configuration**:
   - Update your TwiML Bin to point to: `wss://your-render-app.onrender.com/twilio`
   - Make sure to use `wss://` (WebSocket Secure) protocol

## Features

- Real-time voice conversation
- Menu item lookup
- Order placement
- Order status checking
- Barge-in detection
- Function calling with Supabase integration

## Local Development

1. Install dependencies: `pip install -r requirements.txt`
2. Set up environment variables in `.env` file
3. Run: `python main.py`
4. Use ngrok to expose local server: `ngrok http 5000`
