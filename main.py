import asyncio
import base64
import json
import os
import sys
import websockets
from aiohttp import web
from dotenv import load_dotenv
from restaurant_functions import function_map

# Load environment variables
load_dotenv()

# Synchronous logging function
def log(message):
    print(message, flush=True)
    sys.stdout.flush()

async def validate_deepgram_key():
    """Validate Deepgram API key on startup"""
    api_key = os.getenv('DEEPGRAM_API_KEY')
    if not api_key:
        log("❌ CRITICAL: DEEPGRAM_API_KEY not found")
        return False
    
    test_url = "https://api.deepgram.com/v1/projects"
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(test_url, 
                                 headers={"Authorization": f"Token {api_key}"}) as resp:
                if resp.status != 200:
                    log(f"❌ CRITICAL: Invalid Deepgram key (Status {resp.status})")
                    return False
                else:
                    log("✅ Deepgram API key validated")
                    return True
    except Exception as e:
        log(f"❌ CRITICAL: Deepgram validation failed: {e}")
        return False

async def sts_connect():
    """Connect to Deepgram Voice Agent API"""
    api_key = os.getenv('DEEPGRAM_API_KEY')
    if not api_key:
        log("❌ DEEPGRAM_API_KEY not found in environment variables")
        raise Exception("DEEPGRAM_API_KEY not found")
    
    log(f"🔑 Connecting to Deepgram with API key: {api_key[:10]}...")
    
    try:
        sts_ws = await websockets.connect(
            'wss://agent.deepgram.com/v1/agent/converse',
            extra_headers={
                "Authorization": f"Token {api_key}",
                "Sec-WebSocket-Protocol": "token"  # REQUIRED HEADER
            }
        )
        log("✅ Successfully connected to Deepgram")
        return sts_ws
    except websockets.exceptions.InvalidStatusCode as e:
        log(f"❌ Deepgram authentication failed: {e}")
        raise
    except websockets.exceptions.ConnectionClosed as e:
        log(f"🚨 Deepgram connection closed: {e.code} - {e.reason}")
        raise
    except Exception as e:
        log(f"❌ Failed to connect to Deepgram: {e}")
        raise e

def load_config():
    """Load the voice agent configuration"""
    with open('config.json', 'r') as f:
        return json.load(f)

async def handle_barge_in(decoded, twilio_ws, streams_id):
    """Handle user interrupting the AI"""
    if decoded.get('type') == 'user_started_speaking':
        clear_message = {
            'event': 'clear',
            'streamSid': streams_id
        }
        await twilio_ws.send(json.dumps(clear_message))

async def handle_text_message(decoded, twilio_ws, sts_ws, streams_id):
    """Handle text messages from Deepgram (function calls, etc.)"""
    await handle_barge_in(decoded, twilio_ws, streams_id)
    
    # Handle function calls
    if decoded.get('type') == 'function_call_request':
        await handle_function_call_request(decoded, sts_ws)

async def sts_sender(sts_ws, audio_q):
    """Send audio from queue to Deepgram"""
    print("STS Sender started")
    while True:
        chunk = await audio_q.get()
        await sts_ws.send(chunk)

async def sts_receiver(sts_ws, twilio_ws, streams_id_q):
    """Receive responses from Deepgram and send to Twilio"""
    print("STS Receiver started")
    streams_id = await streams_id_q.get()
    
    async for message in sts_ws:
        if isinstance(message, str):
            decoded = json.loads(message)
            await handle_text_message(decoded, twilio_ws, sts_ws, streams_id)
            continue
        
        # Handle audio response
        raw_mulaw = message
        media_message = {
            'event': 'media',
            'streamSid': streams_id,
            'media': {
                'payload': base64.b64encode(raw_mulaw).decode('ascii')
            }
        }
        await twilio_ws.send(json.dumps(media_message))

async def twilio_receiver(twilio_ws, audio_q, streams_id_q):
    """Receive audio from Twilio and add to queue"""
    buffer_size = 20 * 160
    in_buffer = b''
    
    async for message in twilio_ws:
        try:
            # Handle aiohttp WebSocket messages
            if message.type == web.WSMsgType.TEXT:
                data = json.loads(message.data)
                event = data['event']
                
                if event == 'start':
                    start = data['start']
                    streams_id = start['streamSid']
                    await streams_id_q.put(streams_id)
                    
                elif event == 'connected':
                    continue
                    
                elif event == 'media':
                    media = data['media']
                    chunk = base64.b64decode(media['payload'])
                    
                    if media['track'] == 'inbound':
                        in_buffer += chunk
                        
                elif event == 'stop':
                    break
                    
            elif message.type == web.WSMsgType.ERROR:
                print(f"WebSocket error: {message.data}")
                break
                
        except Exception as e:
            print(f"Error processing Twilio message: {e}")
            continue
    
    # Process buffer
    while len(in_buffer) >= buffer_size:
        chunk = in_buffer[:buffer_size]
        await audio_q.put(chunk)
        in_buffer = in_buffer[buffer_size:]

async def twilio_handler(twilio_ws):
    """Main handler for Twilio WebSocket connections"""
    log("🔗 New Twilio WebSocket connection received")
    audio_q = asyncio.Queue()
    streams_id_q = asyncio.Queue()
    
    # Connect to Deepgram
    log("🔄 Connecting to Deepgram...")
    sts_ws = await sts_connect()
    try:
        # Send configuration to Deepgram
        log("📤 Sending configuration to Deepgram...")
        config_message = load_config()
        await sts_ws.send(json.dumps(config_message))
        log("✅ Configuration sent to Deepgram")
        
        # Start all background tasks
        log("🚀 Starting background tasks...")
        await asyncio.wait([
            asyncio.ensure_future(sts_sender(sts_ws, audio_q)),
            asyncio.ensure_future(sts_receiver(sts_ws, twilio_ws, streams_id_q)),
            asyncio.ensure_future(twilio_receiver(twilio_ws, audio_q, streams_id_q))
        ])
    except websockets.exceptions.ConnectionClosed as e:
        log(f"🚨 Deepgram connection closed during operation: {e.code} - {e.reason}")
        raise e
    except Exception as e:
        log(f"❌ Error in twilio_handler: {e}")
        raise e
    finally:
        log("🔌 Closing connections...")
        await sts_ws.close()
        await twilio_ws.close()

async def handle_function_call_request(decoded, sts_ws):
    """Handle function call requests from Deepgram"""
    try:
        for function_call in decoded.get('functions', []):
            func_name = function_call['name']
            func_id = function_call['id']
            arguments = json.loads(function_call['arguments'])
            
            print(f"Function call: {func_name}, ID: {func_id}, Arguments: {arguments}")
            
            # Execute the function
            result = await execute_function_call(func_name, arguments)
            
            # Create function call response
            function_result = create_function_call_response(func_id, func_name, result)
            
            # Send result back to Deepgram
            await sts_ws.send(json.dumps(function_result))
            print(f"Sent function result: {function_result}")
            
    except Exception as e:
        print(f"Error calling function: {e}")
        error_result = create_function_call_response(
            func_id if 'func_id' in locals() else 'unknown',
            func_name if 'func_name' in locals() else 'unknown',
            {'error': f'Function call failed with {str(e)}'}
        )
        await sts_ws.send(json.dumps(error_result))

def execute_function_call(func_name, arguments):
    """Execute a function call"""
    if func_name in function_map:
        result = function_map[func_name](**arguments)
        print(f"Function call result: {result}")
        return result
    else:
        result = {'error': f'Unknown function: {func_name}'}
        print(f"Function call error: {result}")
        return result

def create_function_call_response(func_id, func_name, result):
    """Create a function call response for Deepgram"""
    return {
        'type': 'function_call_response',
        'id': func_id,
        'name': func_name,
        'content': json.dumps(result)
    }

async def main():
    """Start the server with both HTTP and WebSocket support"""
    
    # Validate Deepgram API key on startup
    log("🔍 Validating Deepgram API key...")
    if not await validate_deepgram_key():
        log("❌ CRITICAL: Deepgram API key validation failed. Exiting.")
        sys.exit(1)
    
    # Create HTTP app
    app = web.Application()
    
    async def health_check(request):
        """Health check endpoint"""
        log(f"🌐 HTTP request to {request.path} from {request.remote}")
        
        # Test environment variables
        deepgram_key = os.getenv('DEEPGRAM_API_KEY')
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        
        status = f"""🚀 Restaurant Voice Server is running!
📡 WebSocket: wss://python-voice-server.onrender.com/twilio

🔑 Environment Variables:
- DEEPGRAM_API_KEY: {'✅ Set' if deepgram_key else '❌ Missing'} ({len(deepgram_key) if deepgram_key else 0} chars)
- SUPABASE_URL: {'✅ Set' if supabase_url else '❌ Missing'} ({len(supabase_url) if supabase_url else 0} chars)
- SUPABASE_SERVICE_ROLE_KEY: {'✅ Set' if supabase_key else '❌ Missing'} ({len(supabase_key) if supabase_key else 0} chars)
"""
        return web.Response(text=status)
    
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    # Add simple test WebSocket endpoint
    async def test_websocket_handler(request):
        """Simple test WebSocket endpoint"""
        log(f"🧪 Test WebSocket request received: {request.path}")
        
        if 'Upgrade' in request.headers and request.headers['Upgrade'].lower() == 'websocket':
            log("✅ Test WebSocket upgrade detected")
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            log("✅ Test WebSocket prepared successfully")
            
            # Send immediate response
            response = {
                "event": "connected",
                "message": "Test WebSocket connection successful"
            }
            await ws.send_str(json.dumps(response))
            log("✅ Test response sent")
            
            await ws.close()
            return ws
        else:
            return web.Response(text="Test WebSocket endpoint", status=426)
    
    app.router.add_get('/test', test_websocket_handler)
    
    # Add WebSocket route
    async def websocket_handler(request):
        """Handle WebSocket upgrade requests"""
        log(f"🔍 WebSocket request received: {request.path}")
        log(f"📋 Headers: {dict(request.headers)}")
        log(f"🌐 Remote: {request.remote}")
        
        # Check if this is a WebSocket upgrade request
        if 'Upgrade' in request.headers and request.headers['Upgrade'].lower() == 'websocket':
            log("✅ WebSocket upgrade detected")
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            log("✅ WebSocket prepared successfully")
            
            if request.path == '/twilio':
                log("🎯 Calling twilio_handler")
                try:
                    await twilio_handler(ws)
                except Exception as e:
                    log(f"❌ Error in twilio_handler: {e}")
                    # Send error response to Twilio
                    error_response = {
                        "event": "error",
                        "error": {
                            "code": "WEBSOCKET_ERROR",
                            "message": str(e)
                        }
                    }
                    await ws.send_str(json.dumps(error_response))
                    await ws.close()
            else:
                log(f"❌ Unknown path: {request.path}")
                await ws.close()
            
            return ws
        else:
            log("❌ Not a WebSocket upgrade request")
            # Return a helpful message for non-WebSocket requests
            return web.Response(
                text="This endpoint requires a WebSocket connection.\nUse: wss://python-voice-server.onrender.com/twilio",
                status=426
            )
    
    app.router.add_get('/twilio', websocket_handler)
    
    # Start server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 5000)
    await site.start()
    
    log("🚀 Restaurant Voice Server started on port 5000!")
    log("🌐 Health check: https://python-voice-server.onrender.com/")
    log("📡 WebSocket endpoint: wss://python-voice-server.onrender.com/twilio")
    
    await asyncio.Future()  # Run forever

if __name__ == "__main__":
    asyncio.run(main())
