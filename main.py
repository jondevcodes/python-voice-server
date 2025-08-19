import asyncio
import base64
import json
import os
import websockets
from aiohttp import web
from dotenv import load_dotenv
from restaurant_functions import function_map

# Load environment variables
load_dotenv()

async def sts_connect():
    """Connect to Deepgram Voice Agent API"""
    api_key = os.getenv('DEEPGRAM_API_KEY')
    if not api_key:
        print("❌ DEEPGRAM_API_KEY not found in environment variables")
        raise Exception("DEEPGRAM_API_KEY not found")
    
    print(f"🔑 Connecting to Deepgram with API key: {api_key[:10]}...")
    
    try:
        sts_ws = await websockets.connect(
            'wss://agent.deepgram.com/v1/agent/converse',
            subprotocols=['token', api_key]
        )
        print("✅ Successfully connected to Deepgram")
        return sts_ws
    except Exception as e:
        print(f"❌ Failed to connect to Deepgram: {e}")
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
    print("🔗 New Twilio WebSocket connection received")
    audio_q = asyncio.Queue()
    streams_id_q = asyncio.Queue()
    
    # Connect to Deepgram
    print("🔄 Connecting to Deepgram...")
    sts_ws = await sts_connect()
    try:
        # Send configuration to Deepgram
        print("📤 Sending configuration to Deepgram...")
        config_message = load_config()
        await sts_ws.send(json.dumps(config_message))
        print("✅ Configuration sent to Deepgram")
        
        # Start all background tasks
        print("🚀 Starting background tasks...")
        await asyncio.wait([
            asyncio.ensure_future(sts_sender(sts_ws, audio_q)),
            asyncio.ensure_future(sts_receiver(sts_ws, twilio_ws, streams_id_q)),
            asyncio.ensure_future(twilio_receiver(twilio_ws, audio_q, streams_id_q))
        ])
    except Exception as e:
        print(f"❌ Error in twilio_handler: {e}")
        raise e
    finally:
        print("🔌 Closing connections...")
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
    
    # Create HTTP app
    app = web.Application()
    
    async def health_check(request):
        """Health check endpoint"""
        return web.Response(text="🚀 Restaurant Voice Server is running!\n📡 WebSocket: wss://python-voice-server.onrender.com/twilio")
    
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    # Add WebSocket route
    async def websocket_handler(request):
        """Handle WebSocket upgrade requests"""
        # Check if this is a WebSocket upgrade request
        if 'Upgrade' in request.headers and request.headers['Upgrade'].lower() == 'websocket':
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            
            if request.path == '/twilio':
                await twilio_handler(ws)
            else:
                await ws.close()
            
            return ws
        else:
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
    
    print("🚀 Restaurant Voice Server started on port 5000!")
    print("🌐 Health check: https://python-voice-server.onrender.com/")
    print("📡 WebSocket endpoint: wss://python-voice-server.onrender.com/twilio")
    
    await asyncio.Future()  # Run forever

if __name__ == "__main__":
    asyncio.run(main())
