import gspread
import qrcode
import uuid
import asyncio
import os
import json
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters

# Configuración
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8368178116:AAHQgckgQb8ODhZtA8zB-CmWy2tY4mQJXHs')
SHEET_ID = os.getenv('GOOGLE_SHEET_ID', '1OWXnqnuFFLWex8Kohfg7zz47GXrl5ZdrvD8265Sdz5s')

SCOPE = ['https://www.googleapis.com/auth/spreadsheets',
         'https://www.googleapis.com/auth/drive']

ADMIN_ID = '634092669'

# Autenticación con Google Sheets desde variables de entorno
try:
    google_creds_json = os.getenv('GOOGLE_CREDENTIALS')
    if not google_creds_json:
        raise Exception("GOOGLE_CREDENTIALS no encontrada en variables de entorno")
    
    creds_dict = json.loads(google_creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
    client = gspread.authorize(creds)
    
    spreadsheet = client.open_by_key(SHEET_ID)
    sheet_registro = spreadsheet.worksheet("registro_clientes")
    sheet_vendedores = spreadsheet.worksheet("Vendedores")
    sheet_historial = spreadsheet.worksheet("HistorialCompras")
    
    print("✅ Conectado a Google Sheets")
    
except Exception as e:
    print(f"❌ Error conectando a Sheets: {e}")
    sheet_registro = None
    sheet_vendedores = None
    sheet_historial = None

# Almacenamiento temporal
codigos_activos = {}
solicitudes_activas = {}
usuarios_agregando_vendedor = set()
usuarios_agregando_cliente = set()
usuarios_eliminando_cliente = set()

# Cache para evitar duplicados
vendedores_cache = {
    'data': [],
    'timestamp': None
}

def limpiar_duplicados_vendedores():
    """Limpia duplicados en la hoja de vendedores"""
    try:
        if not sheet_vendedores:
            return 0
            
        todos_datos = sheet_vendedores.get_all_values()
        if len(todos_datos) <= 1:
            return 0
            
        headers = todos_datos[0]
        datos_vendedores = todos_datos[1:]
        
        vendedores_unicos = {}
        filas_a_eliminar = []
        
        for i, fila in enumerate(datos_vendedores):
            if len(fila) > 0 and fila[0]:
                username = fila[0]
                estado = fila[3] if len(fila) > 3 else 'SI'
                
                if username == ADMIN_ID:
                    continue
                    
                if username in vendedores_unicos and estado == 'SI':
                    filas_a_eliminar.append(i + 2)
                else:
                    vendedores_unicos[username] = True
        
        for fila_num in sorted(filas_a_eliminar, reverse=True):
            sheet_vendedores.delete_rows(fila_num)
        
        if filas_a_eliminar:
            print(f"🧹 Duplicados eliminados: {len(filas_a_eliminar)}")
        
        return len(filas_a_eliminar)
        
    except Exception as e:
        print(f"❌ Error limpiando duplicados: {e}")
        return 0

async def obtener_vendedores_activos():
    """Obtiene lista de vendedores activos desde Google Sheets"""
    global vendedores_cache
    
    try:
        if (vendedores_cache['timestamp'] and 
            (datetime.now() - vendedores_cache['timestamp']).total_seconds() < 300):
            return vendedores_cache['data']
        
        if not sheet_vendedores:
            return []
        
        duplicados_eliminados = limpiar_duplicados_vendedores()
        if duplicados_eliminados > 0:
            print(f"🔄 Se limpiaron {duplicados_eliminados} duplicados")
        
        todos_datos = sheet_vendedores.get_all_values()
        
        if len(todos_datos) <= 1:
            vendedores_cache['data'] = []
            vendedores_cache['timestamp'] = datetime.now()
            return []
        
        headers = todos_datos[0]
        datos_vendedores = todos_datos[1:]
        
        vendedores_activos = []
        vendedores_ids_vistos = set()
        
        for i, fila in enumerate(datos_vendedores, 1):
            if not fila or not any(fila):
                continue
                
            vendedor_dict = {}
            for j, header in enumerate(headers):
                if j < len(fila):
                    vendedor_dict[header] = fila[j]
                else:
                    vendedor_dict[header] = ""
            
            estado = vendedor_dict.get('estado', 'SI')
            username = vendedor_dict.get('username', '')
            nombre = vendedor_dict.get('nombre', 'Sin nombre')
            privilegios = vendedor_dict.get('privilegios', 'normal')
            
            if (estado.upper() == 'SI' and username and 
                username not in vendedores_ids_vistos):
                
                vendedores_ids_vistos.add(username)
                
                vendedor_data = {
                    'user_id': str(username),
                    'nombre': nombre,
                    'privilegios': privilegios
                }
                vendedores_activos.append(vendedor_data)
        
        admin_ya_esta = any(v['user_id'] == ADMIN_ID for v in vendedores_activos)
        if not admin_ya_esta:
            vendedores_activos.append({
                'user_id': ADMIN_ID,
                'nombre': 'Alushi_1 (Admin)',
                'privilegios': 'admin'
            })
            print("✅ Admin agregado como vendedor activo")
        
        vendedores_cache['data'] = vendedores_activos
        vendedores_cache['timestamp'] = datetime.now()
        
        print(f"🎯 Total vendedores activos: {len(vendedores_activos)}")
        return vendedores_activos
        
    except Exception as e:
        print(f"❌ Error obteniendo vendedores: {e}")
        return []

async def es_admin(user_id: str) -> bool:
    """Verifica si el usuario es admin"""
    return user_id == ADMIN_ID

async def es_vendedor(user_id: str) -> bool:
    """Verifica si el usuario es vendedor"""
    vendedores = await obtener_vendedores_activos()
    return any(v['user_id'] == user_id for v in vendedores)

async def es_vendedor_premium(user_id: str) -> bool:
    """Verifica si el usuario es vendedor premium"""
    vendedores = await obtener_vendedores_activos()
    for v in vendedores:
        if v['user_id'] == user_id and v['privilegios'] == 'premium':
            return True
    return False

async def obtener_privilegios_usuario(user_id: str) -> str:
    """Obtiene los privilegios del usuario"""
    if user_id == ADMIN_ID:
        return 'admin'
    
    vendedores = await obtener_vendedores_activos()
    for v in vendedores:
        if v['user_id'] == user_id:
            return v.get('privilegios', 'normal')
    return 'cliente'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /start con diferentes parámetros"""
    user_id = str(update.effective_user.id)
    nombre = update.effective_user.first_name or "Cliente"
    
    try:
        if context.args:
            comando = context.args[0]
            if comando.startswith('compra_'):
                await procesar_compra_qr(update, user_id, comando)
                return
        
        if await es_admin(user_id):
            await mostrar_teclado_admin_completo(update)
            return
            
        privilegios = await obtener_privilegios_usuario(user_id)
        if privilegios == 'premium':
            await mostrar_teclado_vendedor_premium(update)
            return
        elif privilegios == 'normal':
            await mostrar_teclado_vendedor_normal(update)
            return
        
        await mostrar_menu_principal(update, user_id, nombre)
        
    except Exception as e:
        print(f"❌ Error en start: {e}")
        await update.message.reply_text("⚠️ Error temporal. Por favor, intenta nuevamente.")

async def mostrar_teclado_admin_completo(update: Update):
    """Muestra teclado con TODAS las funciones (Admin + Vendedor + Cliente)"""
    keyboard = [
        [KeyboardButton("👤 AGREGAR VENDEDOR NORMAL"), KeyboardButton("🌟 AGREGAR VENDEDOR PREMIUM")],
        [KeyboardButton("🚫 ELIMINAR VENDEDOR"), KeyboardButton("📋 LISTAR VENDEDORES")],
        [KeyboardButton("👥 VER CLIENTES"), KeyboardButton("➕ AGREGAR CLIENTE")],
        [KeyboardButton("🚫 ELIMINAR CLIENTE"), KeyboardButton("💰 MIS VENTAS")],
        [KeyboardButton("📊 ESTADÍSTICAS"), KeyboardButton("🏆 RANKING VENDEDORES")],
        [KeyboardButton("🛒 COMPRAS"), KeyboardButton("📊 MIS SELLOS")],
        [KeyboardButton("📋 MI HISTORIAL"), KeyboardButton("📞 CONTACTAR")],
        [KeyboardButton("🔄 RESET SYSTEM"), KeyboardButton("🏠 INICIO")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    mensaje = "👑 PANEL ADMIN COMPLETO - Shisha MGTA"
    
    await update.message.reply_text(mensaje, reply_markup=reply_markup)

async def mostrar_teclado_vendedor_premium(update: Update):
    """Muestra teclado personalizado para vendedores PREMIUM"""
    keyboard = [
        [KeyboardButton("👥 VER CLIENTES"), KeyboardButton("💰 MIS VENTAS")],
        [KeyboardButton("🏆 RANKING VENDEDORES"), KeyboardButton("📊 ESTADÍSTICAS")],
        [KeyboardButton("📞 CONTACTAR ADMIN")],
        [KeyboardButton("🏠 INICIO")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    mensaje = "🌟 PANEL VENDEDOR PREMIUM - Shisha MGTA"
    
    await update.message.reply_text(mensaje, reply_markup=reply_markup)

async def mostrar_teclado_vendedor_normal(update: Update):
    """Muestra teclado personalizado para vendedores NORMALES"""
    keyboard = [
        [KeyboardButton("👥 VER CLIENTES"), KeyboardButton("💰 MIS VENTAS")],
        [KeyboardButton("📞 CONTACTAR ADMIN")],
        [KeyboardButton("🏠 INICIO")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    mensaje = "👨‍💼 PANEL VENDEDOR NORMAL - Shisha MGTA"
    
    await update.message.reply_text(mensaje, reply_markup=reply_markup)

async def mostrar_menu_principal(update: Update, user_id: str, nombre: str):
    """Muestra el menú principal con botones para clientes"""
    try:
        celda = sheet_registro.find(user_id) if sheet_registro else None
        
        if celda:
            keyboard = [
                [KeyboardButton("🛒 COMPRAS"), KeyboardButton("📊 MIS SELLOS")],
                [KeyboardButton("📋 MI HISTORIAL"), KeyboardButton("ℹ️ INFORMACIÓN")],
                [KeyboardButton("📞 CONTACTAR")],
                [KeyboardButton("🏠 INICIO")]
            ]
            mensaje = f"👋 ¡Hola {nombre}! - Shisha MGTA\n\n¡Estás listo para acumular sellos!"
        else:
            keyboard = [
                [KeyboardButton("📝 REGISTRARME"), KeyboardButton("ℹ️ INFORMACIÓN")],
                [KeyboardButton("📞 CONTACTAR")],
                [KeyboardButton("🏠 INICIO")]
            ]
            mensaje = f"👋 ¡Hola {nombre}! - Shisha MGTA\n\nRegístrate para empezar a acumular sellos"
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(mensaje, reply_markup=reply_markup)
        
    except Exception as e:
        print(f"❌ Error mostrando menú: {e}")
        await update.message.reply_text("¡Bienvenido! Usa /registro para unirte.")

async def manejar_botones_avanzados(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los botones del menú"""
    texto = update.message.text
    user_id = str(update.effective_user.id)
    privilegios = await obtener_privilegios_usuario(user_id)
    
    if texto == "👤 AGREGAR VENDEDOR NORMAL":
        if await es_admin(user_id):
            usuarios_agregando_vendedor.add(user_id)
            await update.message.reply_text(
                "👤 **AGREGAR VENDEDOR NORMAL**\n\n"
                "📱 **Envía el ID y nombre del vendedor:**\n"
                "`123456789 Nombre_Apellido`"
            )
        else:
            await update.message.reply_text("❌ Solo administradores pueden agregar vendedores.")
    
    elif texto == "🌟 AGREGAR VENDEDOR PREMIUM":
        if await es_admin(user_id):
            usuarios_agregando_vendedor.add(user_id)
            await update.message.reply_text(
                "🌟 **AGREGAR VENDEDOR PREMIUM**\n\n"
                "📱 **Envía el ID y nombre del vendedor:**\n"
                "`123456789 Nombre_Apellido`"
            )
        else:
            await update.message.reply_text("❌ Solo administradores pueden agregar vendedores.")
    
    elif texto == "🚫 ELIMINAR VENDEDOR":
        if await es_admin(user_id):
            await mostrar_lista_eliminar_vendedor(update)
        else:
            await update.message.reply_text("❌ Solo administradores pueden eliminar vendedores.")
    
    elif texto == "📋 LISTAR VENDEDORES":
        if await es_admin(user_id):
            await listar_vendedores(update, context)
        else:
            await update.message.reply_text("❌ Solo administradores pueden ver la lista de vendedores.")
    
    elif texto == "👥 VER CLIENTES":
        if await es_admin(user_id):
            await mostrar_clientes_admin(update)
        elif await es_vendedor(user_id):
            await mostrar_clientes_vendedor(update, user_id)
        else:
            await update.message.reply_text("❌ Solo vendedores y administradores pueden ver clientes.")
    
    elif texto == "➕ AGREGAR CLIENTE":
        if await es_admin(user_id):
            usuarios_agregando_cliente.add(user_id)
            await update.message.reply_text(
                "➕ **AGREGAR CLIENTE**\n\n"
                "📱 **Envía el ID y nombre del cliente:**\n"
                "`123456789 Nombre Cliente`"
            )
        else:
            await update.message.reply_text("❌ Solo administradores pueden agregar clientes.")
    
    elif texto == "🚫 ELIMINAR CLIENTE":
        if await es_admin(user_id):
            usuarios_eliminando_cliente.add(user_id)
            await update.message.reply_text(
                "🚫 **ELIMINAR CLIENTE**\n\n"
                "📱 **Envía el ID del cliente a eliminar:**\n"
                "`123456789`"
            )
        else:
            await update.message.reply_text("❌ Solo administradores pueden eliminar clientes.")
    
    elif texto == "💰 MIS VENTAS":
        if await es_vendedor(user_id) or await es_admin(user_id):
            await mostrar_mis_ventas(update, user_id)
        else:
            await update.message.reply_text("❌ Solo vendedores y administradores pueden ver ventas.")
    
    elif texto == "📊 ESTADÍSTICAS":
        if await es_admin(user_id) or privilegios == 'premium':
            estadisticas = await obtener_estadisticas_completas()
            await update.message.reply_text(estadisticas)
        else:
            await update.message.reply_text("❌ Solo administradores y vendedores premium pueden ver estadísticas.")
    
    elif texto == "🏆 RANKING VENDEDORES":
        if await es_admin(user_id) or privilegios == 'premium':
            ranking = await generar_ranking_detallado()
            await update.message.reply_text(ranking)
        else:
            await update.message.reply_text("❌ Solo administradores y vendedores premium pueden ver rankings.")
    
    elif texto == "🛒 COMPRAS" or texto == "🛒 COMPRAR AHORA":
        await solicitar_compra(update, context)
    
    elif texto == "📊 MIS SELLOS":
        await sellos(update, context)
    
    elif texto == "📋 MI HISTORIAL":
        await historial_cliente(update, context)
    
    elif texto == "ℹ️ INFORMACIÓN":
        await info(update, context)
    
    elif texto == "📝 REGISTRARME":
        await registro_directo(update, context)
    
    elif texto == "📞 CONTACTAR" or texto == "📞 CONTACTAR ADMIN":
        await manejar_contacto(update, context)
    
    elif texto == "🔄 RESET SYSTEM":
        if await es_admin(user_id):
            keyboard = [
                [InlineKeyboardButton("✅ SI, RESETEAR SISTEMA", callback_data="confirmar_reset")],
                [InlineKeyboardButton("❌ NO, CANCELAR", callback_data="cancelar_reset")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "🔄 **RESET DEL SISTEMA**\n\n"
                "⚠️ **¿Estás seguro?**\n\n"
                "📊 **Esto limpiará:**\n"
                "• Cache de vendedores\n"
                "• Códigos QR activos\n"
                "• Solicitudes pendientes\n\n"
                "💾 **NO afectará Google Sheets**",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text("❌ Solo el administrador puede resetear el sistema.")
    
    elif texto == "🏠 INICIO":
        await start(update, context)
    
    else:
        if user_id in usuarios_agregando_vendedor:
            await procesar_agregar_vendedor_rapido(update, context)
        elif user_id in usuarios_agregando_cliente:
            await procesar_agregar_cliente(update, context)
        elif user_id in usuarios_eliminando_cliente:
            await procesar_eliminar_cliente(update, context)
        else:
            await start(update, context)

async def procesar_agregar_vendedor_rapido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa el agregado rápido de vendedor"""
    user_id = str(update.effective_user.id)
    texto = update.message.text
    
    try:
        if user_id not in usuarios_agregando_vendedor:
            return
        
        es_premium = "🌟 AGREGAR VENDEDOR PREMIUM" in usuarios_agregando_vendedor
        
        usuarios_agregando_vendedor.discard(user_id)
        
        partes = texto.split(' ', 1)
        if len(partes) != 2:
            await update.message.reply_text("❌ **Formato incorrecto**\n\nUsa: `123456789 Nombre_Apellido`")
            return
        
        nuevo_vendedor_id = partes[0].strip()
        nombre_vendedor = partes[1].strip().replace(' ', '_')
        
        if not nuevo_vendedor_id.isdigit():
            await update.message.reply_text("❌ **ID inválido**\n\nEl ID debe contener solo números.")
            return
        
        try:
            todos_datos = sheet_vendedores.get_all_values()
            if len(todos_datos) > 1:
                datos_vendedores = todos_datos[1:]
                for fila in datos_vendedores:
                    if len(fila) > 0 and str(fila[0]) == nuevo_vendedor_id:
                        estado = fila[3] if len(fila) > 3 else 'SI'
                        if estado.upper() == 'SI':
                            await update.message.reply_text(f"❌ El vendedor {nuevo_vendedor_id} ya existe.")
                            return
        except Exception as e:
            print(f"⚠️ Error verificando duplicados: {e}")
        
        privilegios = "premium" if es_premium else "normal"
        nueva_fila = [
            nuevo_vendedor_id,
            nombre_vendedor,
            datetime.now().strftime("%Y-%m-%d"),
            "SI",
            privilegios
        ]
        
        sheet_vendedores.append_row(nueva_fila)
        
        global vendedores_cache
        nuevo_vendedor_data = {
            'user_id': str(nuevo_vendedor_id),
            'nombre': nombre_vendedor,
            'privilegios': privilegios
        }
        
        if vendedores_cache['data']:
            vendedores_cache['data'].append(nuevo_vendedor_data)
        else:
            vendedores_cache['data'] = [nuevo_vendedor_data]
        
        vendedores_cache['timestamp'] = datetime.now()
        
        vendedores_actualizados = await obtener_vendedores_activos()
        
        tipo_vendedor = "🌟 PREMIUM" if es_premium else "👤 NORMAL"
        await update.message.reply_text(
            f"✅ **Vendedor {tipo_vendedor} agregado**\n\n"
            f"👤 **Nombre:** {nombre_vendedor.replace('_', ' ')}\n"
            f"🆔 **ID:** `{nuevo_vendedor_id}`\n"
            f"🎯 **Privilegios:** {privilegios.upper()}\n"
            f"👥 **Total vendedores:** {len(vendedores_actualizados)}"
        )
        print(f"✅ Vendedor {privilegios} agregado: {nombre_vendedor} ({nuevo_vendedor_id})")
        
    except Exception as e:
        print(f"❌ Error agregando vendedor: {e}")
        await update.message.reply_text(f"❌ Error agregando vendedor: {str(e)}")

async def procesar_agregar_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa el agregado de cliente por admin"""
    user_id = str(update.effective_user.id)
    texto = update.message.text
    
    try:
        if user_id not in usuarios_agregando_cliente:
            return
        
        usuarios_agregando_cliente.discard(user_id)
        
        partes = texto.split(' ', 1)
        if len(partes) != 2:
            await update.message.reply_text("❌ **Formato incorrecto**\n\nUsa: `123456789 Nombre Cliente`")
            return
        
        cliente_id = partes[0].strip()
        nombre_cliente = partes[1].strip()
        
        if not cliente_id.isdigit():
            await update.message.reply_text("❌ **ID inválido**\n\nEl ID debe contener solo números.")
            return
        
        try:
            celda = sheet_registro.find(cliente_id)
            if celda:
                await update.message.reply_text(f"❌ El cliente {cliente_id} ya existe.")
                return
        except:
            pass
        
        nueva_fila = [
            cliente_id,
            "",
            nombre_cliente,
            datetime.now().strftime("%Y-%m-%d"),
            0,
            ""
        ]
        
        sheet_registro.append_row(nueva_fila)
        
        await update.message.reply_text(
            f"✅ **Cliente agregado**\n\n"
            f"👤 **Nombre:** {nombre_cliente}\n"
            f"🆔 **ID:** `{cliente_id}`\n"
            f"📅 **Fecha registro:** {datetime.now().strftime('%Y-%m-%d')}\n"
            f"🏺 **Sellos iniciales:** 0"
        )
        print(f"✅ Cliente agregado por admin: {nombre_cliente} ({cliente_id})")
        
    except Exception as e:
        print(f"❌ Error agregando cliente: {e}")
        await update.message.reply_text(f"❌ Error agregando cliente: {str(e)}")

async def procesar_eliminar_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa la eliminación de cliente por admin"""
    user_id = str(update.effective_user.id)
    texto = update.message.text
    
    try:
        if user_id not in usuarios_eliminando_cliente:
            return
        
        usuarios_eliminando_cliente.discard(user_id)
        
        cliente_id = texto.strip()
        
        if not cliente_id.isdigit():
            await update.message.reply_text("❌ **ID inválido**\n\nEl ID debe contener solo números.")
            return
        
        try:
            celda = sheet_registro.find(cliente_id)
            if not celda:
                await update.message.reply_text(f"❌ Cliente {cliente_id} no encontrado.")
                return
            
            fila = celda.row
            datos_cliente = sheet_registro.row_values(fila)
            nombre_cliente = datos_cliente[2] if len(datos_cliente) > 2 else "Sin nombre"
            
            sheet_registro.delete_rows(fila)
            
            await update.message.reply_text(
                f"✅ **Cliente eliminado**\n\n"
                f"👤 **Nombre:** {nombre_cliente}\n"
                f"🆔 **ID:** `{cliente_id}`\n"
                f"🗑️ **Eliminado por:** Admin"
            )
            print(f"✅ Cliente eliminado por admin: {nombre_cliente} ({cliente_id})")
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error eliminando cliente: {str(e)}")
        
    except Exception as e:
        print(f"❌ Error procesando eliminación: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def mostrar_clientes_admin(update: Update):
    """Muestra todos los clientes para admin"""
    try:
        if not sheet_registro:
            await update.message.reply_text("❌ Error de conexión con Google Sheets.")
            return
        
        todos_datos = sheet_registro.get_all_values()
        
        if len(todos_datos) <= 1:
            await update.message.reply_text("👥 **CLIENTES REGISTRADOS**\n\n📭 No hay clientes registrados aún.")
            return
        
        headers = todos_datos[0]
        datos_clientes = todos_datos[1:]
        
        mensaje = "👥 **TODOS LOS CLIENTES - ADMIN**\n\n"
        
        for i, cliente in enumerate(datos_clientes[-20:][::-1], 1):
            if len(cliente) >= 3:
                user_id_cliente = cliente[0]
                nombre_completo = cliente[2] if len(cliente) > 2 and cliente[2] else f"Usuario_{user_id_cliente}"
                sellos = cliente[3] if len(cliente) > 3 and cliente[3] else "0"
                vendedor = cliente[4] if len(cliente) > 4 and cliente[4] else "Sin asignar"
                
                mensaje += f"{i}. **{nombre_completo}**\n"
                mensaje += f"   🆔 {user_id_cliente} | 🏺 {sellos}/10\n"
                mensaje += f"   👤 {vendedor}\n\n"
        
        total_clientes = len(datos_clientes)
        mensaje += f"📊 **Total clientes:** {total_clientes}"
        
        await update.message.reply_text(mensaje)
        print(f"📋 Admin consultó lista completa de clientes")
        
    except Exception as e:
        print(f"❌ Error mostrando clientes admin: {e}")
        await update.message.reply_text("❌ Error obteniendo datos de clientes.")

async def mostrar_clientes_vendedor(update: Update, user_id: str):
    """Muestra clientes del vendedor específico"""
    try:
        if not sheet_registro:
            await update.message.reply_text("❌ Error de conexión con Google Sheets.")
            return
        
        vendedores = await obtener_vendedores_activos()
        vendedor_actual = next((v for v in vendedores if v['user_id'] == user_id), None)
        
        if not vendedor_actual:
            await update.message.reply_text("❌ No se encontró tu información de vendedor.")
            return
        
        nombre_vendedor = vendedor_actual['nombre']
        
        todos_datos = sheet_registro.get_all_values()
        
        if len(todos_datos) <= 1:
            await update.message.reply_text("👥 **MIS CLIENTES**\n\n📭 No tienes clientes registrados aún.")
            return
        
        headers = todos_datos[0]
        datos_clientes = todos_datos[1:]
        
        clientes_vendedor = []
        for cliente in datos_clientes:
            if len(cliente) > 4 and cliente[4] == nombre_vendedor:
                clientes_vendedor.append(cliente)
        
        if not clientes_vendedor:
            await update.message.reply_text("👥 **MIS CLIENTES**\n\n📭 No tienes clientes registrados aún.")
            return
        
        mensaje = f"👥 **MIS CLIENTES - {nombre_vendedor}**\n\n"
        
        for i, cliente in enumerate(clientes_vendedor[-15:][::-1], 1):
            if len(cliente) >= 3:
                user_id_cliente = cliente[0]
                nombre_completo = cliente[2] if len(cliente) > 2 and cliente[2] else f"Usuario_{user_id_cliente}"
                sellos = cliente[3] if len(cliente) > 3 and cliente[3] else "0"
                
                mensaje += f"{i}. **{nombre_completo}**\n"
                mensaje += f"   🆔 {user_id_cliente} | 🏺 {sellos}/10\n\n"
        
        total_clientes = len(clientes_vendedor)
        clientes_cerca_premio = len([c for c in clientes_vendedor if len(c) > 3 and c[3] and int(c[3]) >= 7])
        
        mensaje += f"📊 **Resumen:**\n"
        mensaje += f"• Total clientes: {total_clientes}\n"
        mensaje += f"• Cerca del premio: {clientes_cerca_premio}\n"
        mensaje += f"• Sellos generados: {sum(int(c[3]) for c in clientes_vendedor if len(c) > 3 and c[3])}"
        
        await update.message.reply_text(mensaje)
        print(f"📋 Vendedor {nombre_vendedor} consultó sus clientes")
        
    except Exception as e:
        print(f"❌ Error mostrando clientes vendedor: {e}")
        await update.message.reply_text("❌ Error obteniendo datos de clientes.")

async def mostrar_mis_ventas(update: Update, user_id: str):
    """Muestra clientes personales del vendedor con sus sellos"""
    try:
        if not sheet_registro:
            await update.message.reply_text("❌ Error de conexión con Google Sheets.")
            return
        
        vendedores = await obtener_vendedores_activos()
        vendedor_actual = next((v for v in vendedores if v['user_id'] == user_id), None)
        
        if not vendedor_actual:
            await update.message.reply_text("❌ No se encontró tu información de vendedor.")
            return
        
        nombre_vendedor = vendedor_actual['nombre']
        privilegios = vendedor_actual['privilegios']
        
        todos_datos = sheet_registro.get_all_values()
        
        if len(todos_datos) <= 1:
            if await es_admin(user_id):
                await update.message.reply_text("💰 **MIS VENTAS - ADMIN**\n\n📭 No hay clientes registrados aún.")
            else:
                await update.message.reply_text("💰 **MIS VENTAS**\n\n📭 No tienes clientes registrados aún.")
            return
        
        headers = todos_datos[0]
        datos_clientes = todos_datos[1:]
        
        if await es_admin(user_id):
            clientes_vendedor = datos_clientes
            titulo = "💰 **TODAS LAS VENTAS - ADMIN**\n\n"
        else:
            clientes_vendedor = [c for c in datos_clientes if len(c) > 4 and c[4] == nombre_vendedor]
            titulo = f"💰 **MIS VENTAS - {nombre_vendedor}**\n\n"
        
        if not clientes_vendedor:
            await update.message.reply_text("💰 **MIS VENTAS**\n\n📭 No tienes clientes registrados aún.")
            return
        
        mensaje = titulo
        
        for i, cliente in enumerate(clientes_vendedor[-10:][::-1], 1):
            if len(cliente) >= 3:
                user_id_cliente = cliente[0]
                nombre_completo = cliente[2] if len(cliente) > 2 and cliente[2] else f"Usuario_{user_id_cliente}"
                sellos = cliente[3] if len(cliente) > 3 and cliente[3] else "0"
                vendedor_asignado = cliente[4] if len(cliente) > 4 and cliente[4] else "Sin asignar"
                
                estado_premio = "🎯 (Cerca del premio!)" if int(sellos) >= 7 else ""
                
                if await es_admin(user_id):
                    mensaje += f"{i}. **{nombre_completo}**\n"
                    mensaje += f"   🆔 {user_id_cliente} | 🏺 {sellos}/10 {estado_premio}\n"
                    mensaje += f"   👤 {vendedor_asignado}\n\n"
                else:
                    mensaje += f"{i}. **{nombre_completo}**\n"
                    mensaje += f"   🆔 {user_id_cliente} | 🏺 {sellos}/10 {estado_premio}\n\n"
        
        total_clientes = len(clientes_vendedor)
        clientes_cerca_premio = len([c for c in clientes_vendedor if len(c) > 3 and c[3] and int(c[3]) >= 7])
        total_sellos = sum(int(c[3]) for c in clientes_vendedor if len(c) > 3 and c[3])
        
        mensaje += f"📊 **Resumen:**\n"
        mensaje += f"• Total clientes: {total_clientes}\n"
        mensaje += f"• Cerca del premio: {clientes_cerca_premio}\n"
        mensaje += f"• Sellos generados: {total_sellos}\n"
        if await es_admin(user_id):
            mensaje += f"• Ingresos estimados: ${total_sellos * 12:,}"
        
        await update.message.reply_text(mensaje)
        print(f"💰 {nombre_vendedor} consultó sus ventas")
        
    except Exception as e:
        print(f"❌ Error mostrando mis ventas: {e}")
        await update.message.reply_text("❌ Error obteniendo datos de ventas.")

async def mostrar_lista_eliminar_vendedor(update: Update):
    """Muestra lista de vendedores para eliminar"""
    try:
        vendedores = await obtener_vendedores_activos()
        vendedores_para_eliminar = [v for v in vendedores if v['user_id'] != ADMIN_ID]
        
        if not vendedores_para_eliminar:
            await update.message.reply_text("❌ No hay vendedores disponibles para eliminar.")
            return
        
        keyboard = []
        for vendedor in vendedores_para_eliminar:
            privilegios_emoji = "🌟" if vendedor['privilegios'] == 'premium' else "👤"
            keyboard.append([InlineKeyboardButton(
                f"🚫 {privilegios_emoji} {vendedor['nombre']} (ID: {vendedor['user_id']})", 
                callback_data=f"eliminar_{vendedor['user_id']}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🚫 **ELIMINAR VENDEDOR - SELECCIONA:**", reply_markup=reply_markup)
        
    except Exception as e:
        print(f"❌ Error mostrando lista eliminar vendedor: {e}")
        await update.message.reply_text("❌ Error cargando lista de vendedores.")

async def manejar_eliminar_vendedor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la eliminación de vendedores desde botones"""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    data = query.data
    
    if not await es_admin(user_id):
        await query.edit_message_text("❌ Solo administradores pueden eliminar vendedores.")
        return
    
    try:
        vendedor_id = data.replace('eliminar_', '')
        
        if vendedor_id == ADMIN_ID:
            await query.edit_message_text("❌ No puedes eliminarte a ti mismo como admin.")
            return
        
        todos_datos = sheet_vendedores.get_all_values()
        datos_vendedores = todos_datos[1:]
        
        vendedor_encontrado = False
        nombre_vendedor = "Sin nombre"
        privilegios_vendedor = "normal"
        
        for i, fila in enumerate(datos_vendedores, start=2):
            if len(fila) > 0 and str(fila[0]) == vendedor_id:
                sheet_vendedores.update_cell(i, 4, "NO")
                nombre_vendedor = fila[1] if len(fila) > 1 else "Sin nombre"
                privilegios_vendedor = fila[4] if len(fila) > 4 else "normal"
                vendedor_encontrado = True
                break
        
        if not vendedor_encontrado:
            await query.edit_message_text("❌ Vendedor no encontrado.")
            return
        
        global vendedores_cache
        if vendedores_cache['data']:
            vendedores_cache['data'] = [v for v in vendedores_cache['data'] if v['user_id'] != vendedor_id]
            vendedores_cache['timestamp'] = datetime.now()
        
        vendedores_actualizados = await obtener_vendedores_activos()
        
        privilegios_emoji = "🌟" if privilegios_vendedor == 'premium' else "👤"
        await query.edit_message_text(
            f"✅ **Vendedor eliminado**\n\n"
            f"{privilegios_emoji} **Nombre:** {nombre_vendedor}\n"
            f"🆔 **ID:** `{vendedor_id}`\n"
            f"🎯 **Privilegios:** {privilegios_vendedor.upper()}\n"
            f"👥 **Vendedores activos:** {len(vendedores_actualizados)}"
        )
        print(f"✅ Vendedor eliminado: {nombre_vendedor} ({vendedor_id})")
        
    except Exception as e:
        print(f"❌ Error eliminando vendedor: {e}")
        await query.edit_message_text("❌ Error eliminando vendedor.")

async def manejar_contacto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el botón de contacto"""
    user_id = str(update.effective_user.id)
    nombre = update.effective_user.first_name or "Usuario"
    
    mensaje_contacto = (
        f"📞 **Contacta al Administrador**\n\n"
        f"👤 **Tu nombre:** {nombre}\n"
        f"🆔 **Tu ID:** `{user_id}`\n\n"
        f"💬 **Para ayuda o consultas:**\n"
        f"👉 @Alushi_1\n\n"
        f"📱 Contacta directamente al admin"
    )
    
    await update.message.reply_text(mensaje_contacto)

async def registrar_usuario(update: Update, user_id: str, nombre: str):
    """Registra un nuevo usuario en el sistema - MEJORADO"""
    try:
        if not sheet_registro:
            await update.message.reply_text("❌ Error del sistema. Intenta más tarde.")
            return
            
        celda = sheet_registro.find(user_id)
        if celda:
            await update.message.reply_text("ℹ️ Ya estás registrado en el programa.")
            return
        
        first_name = update.effective_user.first_name or ""
        last_name = update.effective_user.last_name or ""
        username = f"@{update.effective_user.username}" if update.effective_user.username else ""
        
        nombre_completo = f"{first_name} {last_name}".strip()
        if not nombre_completo or nombre_completo == " ":
            nombre_completo = nombre
        
        sheet_registro.append_row([
            user_id,
            username,
            nombre_completo,
            datetime.now().strftime("%Y-%m-%d"),
            0,
            ""
        ])
        
        mensaje_bienvenida = (
            "🎉 **¡Bienvenidos a la Tarjeta de Promociones de Shisha_Mgta!**\n\n"
            "✅ Ahora participas en nuestro programa de fidelidad\n"
            "🏺 Cada compra de arguile = 1 sello\n"
            "💰 10 sellos = 50% de descuento\n\n"
            "📱 **Para comprar:**\n"
            "• Usa 🛒 COMPRAS\n"
            "• Selecciona tu vendedor\n"
            "• ¡Escanea el QR y listo!"
        )
        
        await update.message.reply_text(mensaje_bienvenida)
        await mostrar_menu_principal(update, user_id, nombre_completo)
        print(f"✅ Nuevo usuario registrado: {nombre_completo} ({user_id})")
        
    except Exception as e:
        print(f"❌ Error registrando usuario: {e}")
        await update.message.reply_text("❌ Error en el registro. Intenta más tarde.")

async def registro_directo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando directo /registro - CORREGIDO PARA PERMITIR ADMIN"""
    user_id = str(update.effective_user.id)
    
    # ✅ SOLO bloquear vendedores NO admin
    if await es_vendedor(user_id) and not await es_admin(user_id):
        await update.message.reply_text(
            "❌ **No puedes registrarte como cliente**\n\n"
            "Eres un vendedor activo del sistema.\n"
            "Si deseas ser cliente, primero debes ser eliminado como vendedor."
        )
        return
    
    nombre = update.effective_user.first_name or "Cliente"
    await registrar_usuario(update, user_id, nombre)

async def solicitar_compra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """El cliente solicita una compra - con selección de vendedor"""
    user_id = str(update.effective_user.id)
    nombre_cliente = update.effective_user.first_name or "Cliente"
    
    try:
        # ✅ PERMITIR AL ADMIN REALIZAR COMPRAS
        if await es_vendedor(user_id) and not await es_admin(user_id):
            await update.message.reply_text("❌ **Los vendedores no pueden realizar compras**\n\nSolo los clientes registrados pueden usar esta función.")
            return
        
        celda = sheet_registro.find(user_id)
        if not celda:
            await update.message.reply_text("🔐 **Primero debes registrarte**\n\nUsa 📝 REGISTRARME")
            return
        
        vendedores = await obtener_vendedores_activos()
        
        if not vendedores:
            await update.message.reply_text("❌ **No hay vendedores disponibles**")
            return
        
        keyboard = []
        for vendedor in vendedores:
            privilegios_emoji = "🌟" if vendedor['privilegios'] == 'premium' else "👤"
            keyboard.append([InlineKeyboardButton(
                f"{privilegios_emoji} {vendedor['nombre']}", 
                callback_data=f"vendedor_{vendedor['user_id']}"
            )])
        
        keyboard.append([InlineKeyboardButton("❓ No sé / Cualquier vendedor", callback_data="vendedor_todos")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        solicitudes_activas[user_id] = {
            'nombre_cliente': nombre_cliente,
            'user_id_cliente': user_id,
            'timestamp': datetime.now()
        }
        
        await update.message.reply_text(
            f"🛒 **Solicitud de Compra**\n\n"
            f"👤 **Cliente:** {nombre_cliente}\n\n"
            f"📋 **¿Qué vendedor te está atendiendo?**\n"
            f"(Selecciona uno de la lista)\n\n"
            f"💡 **El vendedor recibirá tu QR automáticamente**",
            reply_markup=reply_markup
        )
        
        print(f"📦 Solicitud de compra iniciada por {nombre_cliente} ({user_id})")
        
    except Exception as e:
        print(f"❌ Error en solicitud de compra: {e}")
        await update.message.reply_text("❌ Error procesando solicitud.")

async def manejar_seleccion_vendedor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la selección de vendedor y genera QR"""
    query = update.callback_query
    await query.answer()
    
    user_id_cliente = str(query.from_user.id)
    data = query.data
    
    if user_id_cliente not in solicitudes_activas:
        await query.edit_message_text("❌ La solicitud ha expirado. Usa 🛒 COMPRAS nuevamente.")
        return
    
    datos_solicitud = solicitudes_activas[user_id_cliente]
    nombre_cliente = datos_solicitud['nombre_cliente']
    
    try:
        celda = sheet_registro.find(user_id_cliente)
        fila = celda.row
        datos_cliente = sheet_registro.row_values(fila)
        
        while len(datos_cliente) < 5:
            datos_cliente.append("")
        
        sellos_actual = int(datos_cliente[3]) if len(datos_cliente) > 3 and datos_cliente[3] else 0
        
        if data == "vendedor_todos":
            vendedores = await obtener_vendedores_activos()
            vendedores_ids = [v['user_id'] for v in vendedores]
            mensaje_cliente = "📨 **QR enviado a todos los vendedores**"
            vendedor_nombre = "todos los vendedores"
        else:
            vendedor_id = data.replace('vendedor_', '')
            vendedores_ids = [vendedor_id]
            
            vendedores = await obtener_vendedores_activos()
            vendedor_nombre = next((v['nombre'] for v in vendedores if v['user_id'] == vendedor_id), "Vendedor")
            mensaje_cliente = f"📨 **QR enviado a {vendedor_nombre}**"
        
        qr_enviado = await generar_y_enviar_qr_automatico(
            context, nombre_cliente, user_id_cliente, vendedores_ids, vendedor_nombre, sellos_actual
        )
        
        if qr_enviado:
            await query.edit_message_text(
                f"✅ **Solicitud Completada**\n\n"
                f"{mensaje_cliente}\n\n"
                f"👤 **Cliente:** {nombre_cliente}\n"
                f"📊 **Sellos actuales:** {sellos_actual}/10\n\n"
                f"⚡ **El vendedor ya tiene tu QR listo**\n"
                f"¡Acércate para escanearlo! 🏺"
            )
        else:
            await query.edit_message_text("❌ Error generando QR. Intenta nuevamente.")
        
        del solicitudes_activas[user_id_cliente]
        
        print(f"✅ QR generado para {nombre_cliente}, vendedor: {vendedor_nombre}")
        
    except Exception as e:
        print(f"❌ Error en selección de vendedor: {e}")
        await query.edit_message_text("❌ Error procesando selección.")

async def generar_y_enviar_qr_automatico(context: ContextTypes.DEFAULT_TYPE, 
                                       nombre_cliente: str, user_id_cliente: str,
                                       vendedores_ids: list, vendedor_nombre: str,
                                       sellos_actual: int):
    """Genera y envía QR automáticamente al vendedor"""
    try:
        codigo_unico = f"compra_{uuid.uuid4().hex[:8]}_{int(datetime.now().timestamp())}"
        link_compra = f"https://t.me/Shishamgtabot?start={codigo_unico}"
        
        codigos_activos[codigo_unico] = {
            'user_id': user_id_cliente,
            'timestamp': datetime.now(),
            'nombre': nombre_cliente,
            'vendedor': vendedor_nombre
        }
        
        img_qr = qrcode.make(link_compra)
        nombre_archivo = f"qr_auto_{nombre_cliente.replace(' ', '_')}_{int(datetime.now().timestamp())}.png"
        img_qr.save(nombre_archivo)
        
        mensaje_vendedor = (
            f"🏺 **QR AUTOMÁTICO GENERADO**\n\n"
            f"👤 **Cliente:** {nombre_cliente}\n"
            f"📱 **Usuario:** {user_id_cliente}\n"
            f"📊 **Sellos actuales:** {sellos_actual}/10\n"
            f"🎯 **Faltan para premio:** {10 - sellos_actual}\n"
            f"💰 **Valor venta:** $12\n"
            f"⏰ **Hora:** {datetime.now().strftime('%H:%M:%S')}\n"
            f"🔒 **Válido por:** 10 minutos\n\n"
            f"📋 **INSTRUCCIONES:**\n"
            f"1. Muestra este QR al cliente\n"
            f"2. Que lo escanee con su cámara\n"
            f"3. ¡Compra registrada automáticamente! ✅"
        )
        
        qrs_enviados = 0
        with open(nombre_archivo, 'rb') as qr_file:
            for vendedor_id in vendedores_ids:
                try:
                    await context.bot.send_photo(
                        chat_id=vendedor_id,
                        photo=qr_file,
                        caption=mensaje_vendedor
                    )
                    qrs_enviados += 1
                    print(f"📨 QR enviado a vendedor {vendedor_id}")
                    qr_file.seek(0)
                except Exception as e:
                    print(f"❌ Error enviando QR a vendedor {vendedor_id}: {e}")
        
        try:
            os.remove(nombre_archivo)
        except:
            pass
        
        return qrs_enviados > 0
                
    except Exception as e:
        print(f"❌ Error generando QR automático: {e}")
        return False

async def procesar_compra_qr(update: Update, user_id: str, codigo_qr: str):
    """Procesa una compra desde QR único - CON NOTIFICACIÓN AL VENDEDOR"""
    try:
        if not sheet_registro:
            await update.message.reply_text("❌ Error del sistema.")
            return
        
        limpiar_codigos_expirados()
            
        if codigo_qr in codigos_activos:
            datos_qr = codigos_activos[codigo_qr]
            
            if datetime.now() - datos_qr['timestamp'] > timedelta(minutes=10):
                await update.message.reply_text("❌ Este QR ha expirado.")
                del codigos_activos[codigo_qr]
                return
            
            celda = sheet_registro.find(user_id)
            nombre_cliente = datos_qr.get('nombre', update.effective_user.first_name or "Cliente")
            vendedor_actual = datos_qr.get('vendedor', 'vendedor_desconocido')
            
            if not celda:
                first_name = update.effective_user.first_name or ""
                last_name = update.effective_user.last_name or ""
                username = f"@{update.effective_user.username}" if update.effective_user.username else ""
                
                nombre_completo = f"{first_name} {last_name}".strip()
                if not nombre_completo or nombre_completo == " ":
                    nombre_completo = nombre_cliente
                
                sheet_registro.append_row([
                    user_id,
                    username,
                    nombre_completo,
                    datetime.now().strftime("%Y-%m-%d"),
                    1,
                    vendedor_actual
                ])
                
                await update.message.reply_text(
                    "🎉 **¡Bienvenidos a la Tarjeta de Promociones de Shisha_Mgta!**\n\n"
                    "✅ Ahora participas en nuestro programa de fidelidad\n"
                    "🏺 Cada compra de arguile = 1 sello\n"
                    "💰 10 sellos = 50% de descuento\n\n"
                    "📱 **Para comprar:**\n"
                    "• Usa 🛒 COMPRAS\n"
                    "• Selecciona tu vendedor\n"
                    "• ¡Escanea el QR y listo!"
                )
                sellos_actual = 1
            else:
                fila = celda.row
                datos_actuales = sheet_registro.row_values(fila)
                
                while len(datos_actuales) < 6:
                    datos_actuales.append("")
                
                sellos_actual = int(datos_actuales[3]) if datos_actuales[3] else 0
                nuevos_sellos = sellos_actual + 1
                
                sheet_registro.update_cell(fila, 4, nuevos_sellos)
                sheet_registro.update_cell(fila, 5, vendedor_actual)
                sellos_actual = nuevos_sellos
            
            try:
                if sheet_historial:
                    sheet_historial.append_row([
                        user_id,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        vendedor_actual,
                        1,
                        "compra_normal"
                    ])
                    print(f"📝 Historial guardado: {user_id} - {vendedor_actual}")
            except Exception as e:
                print(f"⚠️ Error guardando historial: {e}")
            
            try:
                if vendedor_actual != "todos los vendedores" and vendedor_actual != "vendedor_desconocido":
                    mensaje_vendedor = (
                        f"✅ VENTA CONFIRMADA\n\n"
                        f"👤 Cliente: {nombre_cliente}\n"
                        f"📱 ID: {user_id}\n"
                        f"🏺 Sello sumado: +1\n"
                        f"📊 Total acumulado: {sellos_actual}/10 sellos\n"
                        f"💰 Valor venta: $12\n"
                        f"⏰ Hora: {datetime.now().strftime('%H:%M:%S')}\n\n"
                        f"¡Venta registrada exitosamente! 🎉"
                    )
                    
                    vendedores = await obtener_vendedores_activos()
                    for vendedor in vendedores:
                        if vendedor['nombre'] == vendedor_actual:
                            try:
                                await update._bot.send_message(
                                    chat_id=vendedor['user_id'],
                                    text=mensaje_vendedor
                                )
                                print(f"📨 Notificación enviada al vendedor {vendedor_actual}")
                                break
                            except Exception as e:
                                print(f"❌ Error enviando notificación al vendedor: {e}")
            except Exception as e:
                print(f"⚠️ Error enviando notificación al vendedor: {e}")
            
            celda_actualizada = sheet_registro.find(user_id)
            datos_actualizados = sheet_registro.row_values(celda_actualizada.row)
            sellos_actual = int(datos_actualizados[3]) if len(datos_actualizados) > 3 and datos_actualizados[3] else 0
            
            if sellos_actual >= 10:
                sheet_registro.update_cell(celda_actualizada.row, 4, 0)
                await update.message.reply_text(
                    "🎉 **¡FELICIDADES!** 🎉\n\n"
                    "🏺 **Has completado 10 compras en Shisha MGTA**\n\n"
                    "💰 **PREMIO:** 50% DE DESCUENTO\n"
                    "en tu próxima compra\n\n"
                    "📱 Muestra este mensaje al hacer tu pedido\n"
                    "¡Gracias por tu preferencia!"
                )
                print(f"🎉 Usuario {user_id} ganó 50% descuento")
            else:
                await update.message.reply_text(
                    f"✅ **Compra registrada exitosamente**\n\n"
                    f"🏺 Shisha MGTA agradece tu compra\n\n"
                    f"📊 **Sellos acumulados:** {sellos_actual}/10\n"
                    f"🎯 **Te faltan:** {10 - sellos_actual}\n\n"
                    f"¡Sigue disfrutando de nuestros servicios!"
                )
            
            del codigos_activos[codigo_qr]
            print(f"✅ Compra registrada via QR para usuario {user_id} con vendedor {vendedor_actual}")
            
        else:
            await update.message.reply_text("❌ QR inválido o ya utilizado.")
            
    except Exception as e:
        print(f"❌ Error procesando QR: {e}")
        await update.message.reply_text("❌ Error procesando compra.")

def limpiar_codigos_expirados():
    """Limpia códigos QR expirados"""
    ahora = datetime.now()
    expirados = []
    
    for codigo, datos in codigos_activos.items():
        if ahora - datos['timestamp'] > timedelta(minutes=10):
            expirados.append(codigo)
    
    for codigo in expirados:
        del codigos_activos[codigo]
    
    if expirados:
        print(f"🧹 Códigos expirados limpiados: {len(expirados)}")
    
    return len(expirados)

async def sellos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra los sellos actuales del usuario"""
    user_id = str(update.effective_user.id)
    
    try:
        if not sheet_registro:
            await update.message.reply_text("❌ Error del sistema.")
            return
            
        celda = sheet_registro.find(user_id)
        if celda:
            datos = sheet_registro.row_values(celda.row)
            sellos_actual = int(datos[3]) if len(datos) > 3 and datos[3] else 0
            
            await update.message.reply_text(
                f"📊 Tu progreso en Shisha MGTA\n\n"
                f"🏺 Sellos acumulados: {sellos_actual}/10\n"
                f"🎯 Te faltan {10 - sellos_actual} sellos para tu 50% de descuento\n\n"
                f"¡Sigue comprando para ganar tu premio!"
            )
        else:
            await update.message.reply_text(
                "❌ No estás registrado en el programa.\n\n"
                "Usa el botón 📝 REGISTRARME o escribe /registro"
            )
    except Exception as e:
        print(f"❌ Error en sellos: {e}")
        await update.message.reply_text("❌ Error consultando sellos.")

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra información del programa"""
    mensaje = (
        "🏺 **Shisha MGTA - Programa de Fidelidad**\n\n"
        "💎 **Cómo funciona:**\n"
        "1. Regístrate con 📝 REGISTRARME\n"
        "2. Usa 🛒 COMPRAS y selecciona tu vendedor\n"
        "3. El vendedor recibirá tu QR automáticamente\n"
        "4. Escanea el QR con tu cámara\n"
        "5. ¡Acumula 1 sello por compra!\n"
        "6. Al llegar a 10 sellos: ¡50% DE DESCUENTO!\n\n"
        "🔒 **Seguridad:**\n"
        "• QR únicos por compra\n"
        "• Válidos por 10 minutos\n"
        "• Registro automático\n\n"
        "📞 **¿Preguntas?** Contacta al vendedor"
    )
    await update.message.reply_text(mensaje)

async def historial_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el historial de compras del cliente"""
    user_id = str(update.effective_user.id)
    
    try:
        if not sheet_historial:
            await update.message.reply_text("❌ Sistema de historial no disponible.")
            return
            
        todos_datos = sheet_historial.get_all_values()
        
        if len(todos_datos) <= 1:
            await update.message.reply_text("📭 No tienes compras registradas aún.")
            return
        
        headers = todos_datos[0]
        datos_historial = todos_datos[1:]
        
        compras_cliente = [fila for fila in datos_historial if fila[0] == user_id]
        
        if not compras_cliente:
            await update.message.reply_text("📭 No tienes compras registradas.")
            return
        
        mensaje = "📋 **TU HISTORIAL DE COMPRAS**\n\n"
        
        for compra in compras_cliente[-10:][::-1]:
            fecha = compra[1] if len(compra) > 1 else "Fecha desconocida"
            vendedor = compra[2] if len(compra) > 2 else "Vendedor desconocido"
            
            try:
                fecha_dt = datetime.strptime(fecha, "%Y-%m-%d %H:%M:%S")
                fecha_formateada = fecha_dt.strftime("%d/%m/%Y %H:%M")
            except:
                fecha_formateada = fecha
                
            mensaje += f"📅 {fecha_formateada} - 👤 {vendedor}\n"
        
        total_compras = len(compras_cliente)
        mensaje += f"\n📊 **Total de compras:** {total_compras}"
        mensaje += f"\n🎯 **Te faltan para premio:** {10 - (total_compras % 10)}"
        
        await update.message.reply_text(mensaje)
        print(f"📋 {user_id} consultó su historial de compras")
        
    except Exception as e:
        print(f"❌ Error en historial: {e}")
        await update.message.reply_text("❌ Error obteniendo historial.")

async def listar_vendedores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista todos los vendedores - SOLO ADMIN"""
    user_id = str(update.effective_user.id)
    
    if not await es_admin(user_id):
        await update.message.reply_text("❌ Solo el administrador puede usar este comando.")
        return
    
    try:
        vendedores = await obtener_vendedores_activos()
        
        if not vendedores:
            mensaje = "👥 **VENDEDORES ACTIVOS:**\n• No hay vendedores activos"
        else:
            mensaje = "👥 **VENDEDORES ACTIVOS:**\n"
            for i, vendedor in enumerate(vendedores, 1):
                privilegios_emoji = "👑" if vendedor['user_id'] == ADMIN_ID else ("🌟" if vendedor['privilegios'] == 'premium' else "👤")
                es_admin_str = " (Admin)" if vendedor['user_id'] == ADMIN_ID else ""
                privilegios_str = f" - {vendedor['privilegios'].upper()}" if vendedor['user_id'] != ADMIN_ID else ""
                mensaje += f"{i}. {privilegios_emoji} {vendedor['nombre']} (ID: {vendedor['user_id']}){es_admin_str}{privilegios_str}\n"
        
        total_general = len(vendedores)
        vendedores_normales = [v for v in vendedores if v['user_id'] != ADMIN_ID and v['privilegios'] == 'normal']
        vendedores_premium = [v for v in vendedores if v['user_id'] != ADMIN_ID and v['privilegios'] == 'premium']
        total_eliminables = len(vendedores_normales) + len(vendedores_premium)
        
        mensaje += f"\n📊 **Total en sistema:** {total_general} vendedores"
        mensaje += f"\n👤 **Vendedores normales:** {len(vendedores_normales)}"
        mensaje += f"\n🌟 **Vendedores premium:** {len(vendedores_premium)}"
        if total_general > total_eliminables:
            mensaje += f"\n👑 **Eres el admin** (no puedes eliminarte)"
        if total_eliminables > 0:
            mensaje += f"\n🚫 **Disponibles para eliminar:** {total_eliminables} vendedores"
        
        await update.message.reply_text(mensaje)
        
    except Exception as e:
        print(f"❌ Error listando vendedores: {e}")
        await update.message.reply_text("❌ Error listando vendedores.")

async def generar_ranking_detallado():
    """🏆 GENERA RANKING DETALLADO DE VENDEDORES"""
    try:
        if not sheet_historial or not sheet_vendedores:
            return "📊 RANKING VENDEDORES\n❌ No hay datos disponibles"
        
        datos_historial = sheet_historial.get_all_values()
        datos_vendedores = sheet_vendedores.get_all_values()
        
        if len(datos_historial) <= 1:
            return "📊 RANKING VENDEDORES\n📭 No hay ventas registradas"
        
        stats_vendedores = {}
        
        for venta in datos_historial[1:]:
            if len(venta) > 2 and venta[2]:
                vendedor = venta[2]
                if vendedor not in stats_vendedores:
                    stats_vendedores[vendedor] = {
                        'ventas': 0,
                        'clientes_unicos': set(),
                        'ultima_venta': venta[1] if len(venta) > 1 else '',
                        'total_sellos': 0
                    }
                
                stats_vendedores[vendedor]['ventas'] += 1
                if len(venta) > 0 and venta[0]:
                    stats_vendedores[vendedor]['clientes_unicos'].add(venta[0])
        
        datos_registro = sheet_registro.get_all_values()
        if len(datos_registro) > 1:
            for cliente in datos_registro[1:]:
                if len(cliente) > 4 and cliente[4] in stats_vendedores:
                    if len(cliente) > 3 and cliente[3]:
                        try:
                            stats_vendedores[cliente[4]]['total_sellos'] += int(cliente[3])
                        except:
                            pass
        
        ranking_ordenado = sorted(stats_vendedores.items(), 
                                key=lambda x: x[1]['ventas'], 
                                reverse=True)
        
        mensaje_ranking = "🏆 TOP VENDEDORES\n\n"
        
        emojis_podio = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for i, (vendedor, stats) in enumerate(ranking_ordenado[:10]):
            emoji = emojis_podio[i] if i < len(emojis_podio) else f"{i+1}."
            ventas = stats['ventas']
            clientes_unicos = len(stats['clientes_unicos'])
            sellos = stats['total_sellos']
            
            eficiencia = (ventas / clientes_unicos) if clientes_unicos > 0 else 0
            
            mensaje_ranking += (
                f"{emoji} {vendedor}\n"
                f"   📦 {ventas} ventas | "
                f"👥 {clientes_unicos} clientes\n"
                f"   🏷️ {sellos} sellos | "
                f"📊 {eficiencia:.1f} vta/cli\n"
                f"   💰 ${ventas * 12:,} ingresos\n\n"
            )
        
        total_ventas_ranking = sum(stats['ventas'] for stats in stats_vendedores.values())
        total_vendedores_ranking = len(stats_vendedores)
        promedio_ventas = total_ventas_ranking / total_vendedores_ranking if total_vendedores_ranking > 0 else 0
        
        mensaje_ranking += f"📈 RESUMEN RANKING\n"
        mensaje_ranking += f"• Total ventas: {total_ventas_ranking}\n"
        mensaje_ranking += f"• Vendedores activos: {total_vendedores_ranking}\n"
        mensaje_ranking += f"• Promedio: {promedio_ventas:.1f} ventas/vendedor\n"
        mensaje_ranking += f"• Ingresos totales: ${total_ventas_ranking * 12:,}\n"
        
        if ranking_ordenado:
            mejor_vendedor = ranking_ordenado[0]
            mensaje_ranking += f"• 🏅 Mejor: {mejor_vendedor[0]} ({mejor_vendedor[1]['ventas']} ventas = ${mejor_vendedor[1]['ventas'] * 12:,})"
        
        return mensaje_ranking
        
    except Exception as e:
        return f"📊 RANKING VENDEDORES\n❌ Error: {str(e)}"

async def obtener_estadisticas_completas():
    """📊 ESTADÍSTICAS COMPLETAS DEL SISTEMA"""
    try:
        if not sheet_registro or not sheet_vendedores or not sheet_historial:
            return "❌ Error de conexión con Google Sheets"
        
        datos_registro = sheet_registro.get_all_values()
        datos_vendedores = sheet_vendedores.get_all_values()
        datos_historial = sheet_historial.get_all_values()
        
        total_clientes = len(datos_registro) - 1 if len(datos_registro) > 1 else 0
        total_vendedores = len(datos_vendedores) - 1 if len(datos_vendedores) > 1 else 0
        total_ventas = len(datos_historial) - 1 if len(datos_historial) > 1 else 0
        
        activos_count = 0
        inactivos_count = 0
        vendedores_normales = 0
        vendedores_premium = 0
        if len(datos_vendedores) > 1:
            for vendedor in datos_vendedores[1:]:
                if len(vendedor) > 3:
                    if vendedor[3].upper() == 'SI':
                        activos_count += 1
                        if len(vendedor) > 4:
                            if vendedor[4] == 'premium':
                                vendedores_premium += 1
                            else:
                                vendedores_normales += 1
                    else:
                        inactivos_count += 1
        
        ranking_simple = await generar_ranking_detallado()
        
        total_sellos = 0
        clientes_con_sellos = 0
        clientes_cerca_premio = 0
        hoy = datetime.now().strftime("%Y-%m-%d")
        clientes_nuevos_hoy = 0
        ventas_hoy = 0
        
        if len(datos_registro) > 1:
            for cliente in datos_registro[1:]:
                if len(cliente) > 3 and cliente[3]:
                    try:
                        sellos_cliente = int(cliente[3])
                        total_sellos += sellos_cliente
                        if sellos_cliente > 0:
                            clientes_con_sellos += 1
                        if 7 <= sellos_cliente <= 9:
                            clientes_cerca_premio += 1
                    except:
                        pass
                
                if len(cliente) > 2 and cliente[2] == hoy:
                    clientes_nuevos_hoy += 1
        
        if len(datos_historial) > 1:
            for venta in datos_historial[1:]:
                if len(venta) > 1 and venta[1].startswith(hoy):
                    ventas_hoy += 1
        
        estadisticas = f"""
🏆 ESTADÍSTICAS COMPLETAS - SHISHA MGTA

👥 CLIENTES
• Total registrados: {total_clientes}
• Nuevos hoy: {clientes_nuevos_hoy}
• Con sellos: {clientes_con_sellos}
• Cerca de premio: {clientes_cerca_premio}
• Tasa actividad: {(clientes_con_sellos/total_clientes*100) if total_clientes > 0 else 0:.1f}%

💰 VENTAS & SELLOS
• Total sellos: {total_sellos}
• Ventas totales: {total_ventas}
• Ventas hoy: {ventas_hoy}
• Ingresos estimados: ${total_ventas * 12:,}

👨‍💼 VENDEDORES
• Total en sistema: {total_vendedores}
• Activos: {activos_count}
• Inactivos: {inactivos_count}
• 👤 Normales: {vendedores_normales}
• 🌟 Premium: {vendedores_premium}

{ranking_simple}

🔮 PROYECCIONES
• Premios próximos: {clientes_cerca_premio} clientes
• Ingreso/día: ${(ventas_hoy * 12):,}
• Ritmo: {ventas_hoy} ventas/hoy

⏰ Actualizado: {datetime.now().strftime('%H:%M:%S')}
"""
        
        return estadisticas
        
    except Exception as e:
        return f"❌ Error obteniendo estadísticas: {str(e)}"

async def reset_system():
    """🔄 LIMPIA TODOS LOS CACHES Y RESETEA SISTEMA - SOLO ADMIN"""
    global vendedores_cache, codigos_activos, solicitudes_activas
    
    try:
        vendedores_cache = {
            'data': [],
            'timestamp': None
        }
        
        codigos_limpiados = len(codigos_activos)
        codigos_activos = {}
        
        solicitudes_limpiadas = len(solicitudes_activas)
        solicitudes_activas = {}
        
        usuarios_limpiados = len(usuarios_agregando_vendedor)
        usuarios_agregando_vendedor.clear()
        usuarios_agregando_cliente.clear()
        usuarios_eliminando_cliente.clear()
        
        print("🔄 RESET SYSTEM ejecutado - Limpiando todos los caches")
        
        return {
            'codigos_limpiados': codigos_limpiados,
            'solicitudes_limpiadas': solicitudes_limpiadas,
            'usuarios_limpiados': usuarios_limpiados
        }
        
    except Exception as e:
        print(f"❌ Error en reset system: {e}")
        return None

async def manejar_reset_system(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la confirmación del reset del sistema"""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    data = query.data
    
    if not await es_admin(user_id):
        await query.edit_message_text("❌ Solo el administrador puede resetear el sistema.")
        return
    
    if data == "confirmar_reset":
        resultado = await reset_system()
        
        if resultado:
            await query.edit_message_text(
                f"✅ **SISTEMA RESETEADO**\n\n"
                f"🧹 **Elementos limpiados:**\n"
                f"• {resultado['codigos_limpiados']} códigos QR\n"
                f"• {resultado['solicitudes_limpiadas']} solicitudes\n"
                f"• {resultado['usuarios_limpiados']} usuarios temporales\n\n"
                f"🔄 **Todos los caches han sido limpiados**\n"
                f"📊 **Los datos ahora están sincronizados con Google Sheets**\n\n"
                f"¡Sistema listo para usar con datos actualizados! 🎉"
            )
            print(f"🔄 Sistema reseteado por admin {user_id}")
        else:
            await query.edit_message_text("❌ Error al resetear el sistema.")
    
    elif data == "cancelar_reset":
        await query.edit_message_text("❌ Reset del sistema cancelado.")

# HANDLERS PRINCIPALES
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('registro', registro_directo))
    app.add_handler(CommandHandler('compras', solicitar_compra))
    app.add_handler(CallbackQueryHandler(manejar_seleccion_vendedor, pattern='^vendedor_'))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_botones_avanzados))
    
    app.add_handler(CallbackQueryHandler(manejar_eliminar_vendedor, pattern='^eliminar_'))
    app.add_handler(CallbackQueryHandler(manejar_reset_system, pattern='^(confirmar_reset|cancelar_reset)$'))
    
    app.add_handler(CommandHandler('agregarvendedor', manejar_botones_avanzados))
    app.add_handler(CommandHandler('eliminarvendedor', manejar_botones_avanzados))
    app.add_handler(CommandHandler('listarvendedores', listar_vendedores))
    
    app.add_handler(CommandHandler('clientes', manejar_botones_avanzados))
    app.add_handler(CommandHandler('compras_vendedor', manejar_botones_avanzados))
    
    app.add_handler(CommandHandler('sellos', sellos))
    app.add_handler(CommandHandler('estado', sellos))
    app.add_handler(CommandHandler('info', info))
    
    app.add_handler(CommandHandler('historial', historial_cliente))
    app.add_handler(CommandHandler('ranking', generar_ranking_detallado))
    
    print("🚀 Shisha MGTA Bot - INICIADO")
    print("✅ FUNCIONALIDADES ACTIVAS:")
    print("   • 🎯 Sistema de privilegios (normal/premium)")
    print("   • 👤 Agregar vendedor normal")
    print("   • 🌟 Agregar vendedor premium") 
    print("   • 👥 Ver clientes (admin/vendedores)")
    print("   • ➕ Agregar cliente (admin)")
    print("   • 🚫 Eliminar cliente (admin)")
    print("   • 💰 Mis ventas con sellos por cliente")
    print("   • 🛒 Botón COMPRAS para clientes registrados")
    print("   • 📊 Estadísticas con privilegios")
    print("   • 🏆 Ranking con privilegios")
    print("   • 🔔 Notificación al vendedor")
    print("   • 📋 Historial de compras")
    print("   • 👑 Panel admin completo")
    print("   • 🔄 BOTÓN RESET SYSTEM para admin")
    print("📊 Conectado a Google Sheets")
    print("🏺 Sistema de fidelidad activo")
    print("📱 QR únicos habilitados")
    print("⚡ Botones rápidos funcionando")
    print("☁️ Listo para hosting 24/7")
    print("─" * 50)
    

    app.run_polling()
