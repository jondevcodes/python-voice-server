import os
from supabase import create_client, Client

def get_supabase_client():
    """Get Supabase client with environment variables"""
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    if not supabase_url or not supabase_key:
        raise Exception("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
    return create_client(supabase_url, supabase_key)

def get_menu_item(item_name):
    """Get information about a menu item"""
    try:
        supabase = get_supabase_client()
        # Query the menu_items table
        response = supabase.table('menu_items').select('*').ilike('name', f'%{item_name}%').execute()
        
        if response.data:
            item = response.data[0]
            return {
                'name': item['name'],
                'description': item['description'],
                'price': item['price'],
                'category': item['category']
            }
        else:
            return {'error': f'Menu item "{item_name}" not found'}
    except Exception as e:
        return {'error': f'Error looking up menu item: {str(e)}'}

def place_order(customer_name, item_name):
    """Place a new order"""
    try:
        supabase = get_supabase_client()
        # First, get the menu item
        menu_response = supabase.table('menu_items').select('*').ilike('name', f'%{item_name}%').execute()
        
        if not menu_response.data:
            return {'error': f'Menu item "{item_name}" not found'}
        
        menu_item = menu_response.data[0]
        
        # Create the order
        order_data = {
            'customer_name': customer_name,
            'item_name': menu_item['name'],
            'price': menu_item['price'],
            'status': 'pending'
        }
        
        response = supabase.table('orders').insert(order_data).execute()
        
        if response.data:
            order = response.data[0]
            return {
                'order_id': order['id'],
                'customer_name': order['customer_name'],
                'item_name': order['item_name'],
                'price': order['price'],
                'status': order['status']
            }
        else:
            return {'error': 'Failed to create order'}
    except Exception as e:
        return {'error': f'Error placing order: {str(e)}'}

def lookup_order(order_id):
    """Look up an existing order"""
    try:
        supabase = get_supabase_client()
        response = supabase.table('orders').select('*').eq('id', order_id).execute()
        
        if response.data:
            order = response.data[0]
            return {
                'order_id': order['id'],
                'customer_name': order['customer_name'],
                'item_name': order['item_name'],
                'price': order['price'],
                'status': order['status'],
                'created_at': order['created_at']
            }
        else:
            return {'error': f'Order {order_id} not found'}
    except Exception as e:
        return {'error': f'Error looking up order: {str(e)}'}

# Function mapping for the voice agent
function_map = {
    'get_menu_item': get_menu_item,
    'place_order': place_order,
    'lookup_order': lookup_order
}
