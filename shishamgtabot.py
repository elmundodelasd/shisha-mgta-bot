import gspread
import qrcode
import uuid
import asyncio
import os
import pandas as pd
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters

# Configuración
CRED_PATH = os.getenv('GOOGLE_CRED_PATH', 'googlecred.json')
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8368178116:AAHQgckgQb8ODhZtA8zB-CmWy2tY4mQJXHs')

SCOPE = ['https://www.googleapis.com/auth/spreadsheets',
         'https://www.googleapis.com/auth/drive']

ADMIN_ID = '634092669'  # ✅ TU ID

# Autenticación con Google Sheets
try:
    creds = Credentials.from_service_account_file(CRED_PATH, scopes=SCOPE)
    client = gspread.authorize(creds)
    
    # Conectar a las hojas actualizadas
    sheet_registro = client.open("promo sellos bot").worksheet("registro_clientes")
    sheet_vendedores = client.open("promo sellos bot").worksheet("Vendedores")
    sheet_historial = client.open("promo sellos bot").worksheet("HistorialCompras")
    
    print("✅ Conectado a Google Sheets (registro_clientes, Vendedores, HistorialCompras)")
    
except Exception as e:
    print(f"❌ Error conectando a Sheets: {e}")
    sheet_registro = None
    sheet_vendedores = None
    sheet_historial = None

# Almacenamiento temporal
codigos_activos = {}
solicitudes_activas = {}
usuarios_agregando_vendedor = set()

# Cache para evitar duplicados
vendedores_cache = {
    'data': [],
    'timestamp': None
}

def limpiar_duplicados_vendedores():
    """Limpia duplicados en la hoja de vendedores - CON 1 FILA HEADER"""
    try:
        if not sheet_vendedores:
            return 0
            
        todos_datos = sheet_vendedores.get_all_values()
        if len(todos_datos) <= 1:  # ✅ CAMBIO: 1 fila header
            return 0
            
        headers = todos_datos[0]  # ✅ Fila 0 = Headers
        datos_vendedores = todos_datos[1:]  # ✅ Fila 1+ = Datos
        
        vendedores_unicos = {}
        filas_a_eliminar = []
        
        for i, fila in enumerate(datos_vendedores):
            if len(fila) > 0 and fila[0]:
                username = fila[0]
                estado = fila[3] if len(fila) > 3 else 'SI'
                if username in vendedores_unicos and estado == 'SI':
                    filas_a_eliminar.append(i + 2)  # ✅ +2 porque ahora empieza en fila 2
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
    """Obtiene lista de vendedores activos desde Google Sheets - CON 1 FILA HEADER"""
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
        
        # ✅ CAMBIO AQUÍ: Solo 1 fila de headers
        if len(todos_datos) <= 1:
            vendedores_cache['data'] = []
            vendedores_cache['timestamp'] = datetime.now()
            return []
        
        headers = todos_datos[0]  # ✅ Fila 0 = Headers (no Fila 1)
        datos_vendedores = todos_datos[1:]  # ✅ Fila 1+ = Datos (no Fila 2+)
        
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
            
            if (estado.upper() == 'SI' and username and 
                username not in vendedores_ids_vistos):
                
                vendedores_ids_vistos.add(username)
                
                vendedor_data = {
                    'user_id': str(username),
                    'nombre': nombre
                }
                vendedores_activos.append(vendedor_data)
        
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
            await mostrar_teclado_admin(update)
            return
            
        if await es_vendedor(user_id):
            await mostrar_teclado_vendedor(update)
            return
        
        await mostrar_menu_principal(update, user_id, nombre)
        
    except Exception as e:
        print(f"❌ Error en start: {e}")
        await update.message.reply_text("⚠️ Error temporal. Por favor, intenta nuevamente.")

async def mostrar_teclado_admin(update: Update):
    """Muestra teclado personalizado para admin"""
    keyboard = [
        [KeyboardButton("👤 AGREGAR VENDEDOR"), KeyboardButton("🚫 ELIMINAR VENDEDOR")],
        [KeyboardButton("📋 LISTAR VENDEDORES"), KeyboardButton("📊 ESTADÍSTICAS")],
        [KeyboardButton("🏆 RANKING VENDEDORES"), KeyboardButton("👥 VER CLIENTES")],
        [KeyboardButton("💰 MIS VENTAS"), KeyboardButton("🏠 INICIO")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    mensaje = "👑 PANEL ADMIN - Shisha MGTA"
    
    await update.message.reply_text(mensaje, reply_markup=reply_markup)

async def mostrar_teclado_vendedor(update: Update):
    """Muestra teclado personalizado para vendedores"""
    keyboard = [
        [KeyboardButton("👥 VER CLIENTES"), KeyboardButton("💰 MIS VENTAS")],
        [KeyboardButton("🏠 INICIO")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    mensaje = "👨‍💼 PANEL VENDEDOR - Shisha MGTA"
    
    await update.message.reply_text(mensaje, reply_markup=reply_markup)

async def mostrar_menu_principal(update: Update, user_id: str, nombre: str):
    """Muestra el menú principal con botones para clientes"""
    try:
        celda = sheet_registro.find(user_id) if sheet_registro else None
        
        if celda:
            keyboard = [
                [KeyboardButton("🛒 COMPRAS"), KeyboardButton("📊 MIS SELLOS")],
                [KeyboardButton("📋 MI HISTORIAL"), KeyboardButton("ℹ️ INFORMACIÓN")],
                [KeyboardButton("🏠 INICIO")]
            ]
            mensaje = f"👋 ¡Hola {nombre}! - Shisha MGTA"
        else:
            keyboard = [
                [KeyboardButton("📝 REGISTRARME"), KeyboardButton("ℹ️ INFORMACIÓN")],
                [KeyboardButton("🏠 INICIO")]
            ]
            mensaje = f"👋 ¡Hola {nombre}! - Shisha MGTA"
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(mensaje, reply_markup=reply_markup)
        
    except Exception as e:
        print(f"❌ Error mostrando menú: {e}")
        await update.message.reply_text("¡Bienvenido! Usa /registro para unirte.")

async def manejar_botones_avanzados(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los botones del menú"""
    texto = update.message.text
    user_id = str(update.effective_user.id)
    
    if texto == "👤 AGREGAR VENDEDOR":
        if await es_admin(user_id):
            usuarios_agregando_vendedor.add(user_id)
            await update.message.reply_text(
                "👤 **AGREGAR VENDEDOR - MODO RÁPIDO**\n\n"
                "📱 **Envía el ID y nombre del vendedor en este formato:**\n"
                "`123456789 Nombre_Apellido`\n\n"
                "📋 **Ejemplo:**\n"
                "`123456789 Juan_Perez`\n\n"
                "⚠️ **Solo escribe los datos, sin comandos:**"
            )
        else:
            await update.message.reply_text("❌ Solo administradores pueden agregar vendedores.")
    
    elif texto == "🚫 ELIMINAR VENDEDOR":
        if await es_admin(user_id):
            vendedores = await obtener_vendedores_activos()
            vendedores_para_eliminar = [v for v in vendedores if v['user_id'] != ADMIN_ID]
            
            if not vendedores_para_eliminar:
                await update.message.reply_text("❌ No hay vendedores disponibles para eliminar.")
                return
            
            keyboard = []
            for vendedor in vendedores_para_eliminar:
                keyboard.append([InlineKeyboardButton(
                    f"🚫 {vendedor['nombre']} (ID: {vendedor['user_id']})", 
                    callback_data=f"eliminar_{vendedor['user_id']}"
                )])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("🚫 **ELIMINAR VENDEDOR - SELECCIONA:**", reply_markup=reply_markup)
    
    elif texto == "📋 LISTAR VENDEDORES":
        if await es_admin(user_id):
            await listar_vendedores(update, context)
        else:
            await update.message.reply_text("❌ Solo administradores pueden ver la lista de vendedores.")
    
    elif texto == "📊 ESTADÍSTICAS":
        if await es_admin(user_id):
            estadisticas = await obtener_estadisticas_actualizadas()
            await update.message.reply_text(estadisticas)
        else:
            await update.message.reply_text("❌ Solo administradores pueden ver estadísticas completas.")
    
    elif texto == "🏆 RANKING VENDEDORES":
        if await es_admin(user_id):
            await ranking_vendedores(update, context)
        else:
            await update.message.reply_text("❌ Solo administradores pueden ver rankings.")
    
    elif texto == "👥 VER CLIENTES":
        if await es_vendedor(user_id) or await es_admin(user_id):
            await clientes_vendedor(update, context)
        else:
            await update.message.reply_text("❌ Solo vendedores y administradores pueden ver clientes.")
    
    elif texto == "💰 MIS VENTAS":
        if await es_vendedor(user_id) or await es_admin(user_id):
            await compras_vendedor(update, context)
        else:
            await update.message.reply_text("❌ Solo vendedores y administradores pueden ver ventas.")
    
    elif texto == "🛒 COMPRAS":
        await solicitar_compra(update, context)
    
    elif texto == "📊 MIS SELLOS":
        await sellos(update, context)
    
    elif texto == "📋 MI HISTORIAL":
        await historial_cliente(update, context)
    
    elif texto == "ℹ️ INFORMACIÓN":
        await info(update, context)
    
    elif texto == "📝 REGISTRARME":
        await registro_directo(update, context)
    
    elif texto == "🏠 INICIO":
        await start(update, context)
    
    else:
        if user_id in usuarios_agregando_vendedor:
            await procesar_agregar_vendedor_rapido(update, context)
        else:
            await start(update, context)

async def procesar_agregar_vendedor_rapido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa el agregado rápido de vendedor"""
    user_id = str(update.effective_user.id)
    texto = update.message.text
    
    try:
        if user_id not in usuarios_agregando_vendedor:
            return
        
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
        
        # Verificar duplicados
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
        
        # Agregar vendedor
        nueva_fila = [
            nuevo_vendedor_id,
            nombre_vendedor,
            datetime.now().strftime("%Y-%m-%d"),
            "SI"
        ]
        
        sheet_vendedores.append_row(nueva_fila)
        
        # Actualizar cache
        global vendedores_cache
        nuevo_vendedor_data = {
            'user_id': str(nuevo_vendedor_id),
            'nombre': nombre_vendedor
        }
        
        if vendedores_cache['data']:
            vendedores_cache['data'].append(nuevo_vendedor_data)
        else:
            vendedores_cache['data'] = [nuevo_vendedor_data]
        
        vendedores_cache['timestamp'] = datetime.now()
        
        vendedores_actualizados = await obtener_vendedores_activos()
        
        await update.message.reply_text(
            f"✅ **Vendedor agregado EXITOSAMENTE**\n\n"
            f"👤 **Nombre:** {nombre_vendedor.replace('_', ' ')}\n"
            f"🆔 **ID:** `{nuevo_vendedor_id}`\n"
            f"👥 **Total vendedores:** {len(vendedores_actualizados)}"
        )
        print(f"✅ Vendedor agregado: {nombre_vendedor} ({nuevo_vendedor_id})")
        
    except Exception as e:
        print(f"❌ Error agregando vendedor: {e}")
        await update.message.reply_text(f"❌ Error agregando vendedor: {str(e)}")

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
        
        for i, fila in enumerate(datos_vendedores, start=2):
            if len(fila) > 0 and str(fila[0]) == vendedor_id:
                sheet_vendedores.update_cell(i, 4, "NO")
                nombre_vendedor = fila[1] if len(fila) > 1 else "Sin nombre"
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
        
        await query.edit_message_text(
            f"✅ **Vendedor eliminado EXITOSAMENTE**\n\n"
            f"👤 **Nombre:** {nombre_vendedor}\n"
            f"🆔 **ID:** `{vendedor_id}`\n"
            f"👥 **Total vendedores activos:** {len(vendedores_actualizados)}"
        )
        print(f"✅ Vendedor eliminado: {nombre_vendedor} ({vendedor_id})")
        
    except Exception as e:
        print(f"❌ Error eliminando vendedor: {e}")
        await query.edit_message_text("❌ Error eliminando vendedor.")

async def registrar_usuario(update: Update, user_id: str, nombre: str):
    """Registra un nuevo usuario en el sistema"""
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
        
        sheet_registro.append_row([
            user_id,
            username,
            datetime.now().strftime("%Y-%m-%d"),
            0,
            ""
        ])
        
        nombre_completo = f"{first_name} {last_name}".strip()
        await update.message.reply_text(
            f"🎉 **¡Registro exitoso, {nombre_completo}!**\n\n"
            f"✅ Ahora participas en nuestro programa de fidelidad\n"
            f"🏺 Cada compra de arguile = 1 sello\n"
            f"💰 10 sellos = 50% de descuento\n\n"
            f"📱 **Para comprar:**\n"
            f"• Usa el botón 🛒 COMPRAS\n"
            f"• Selecciona tu vendedor\n"
            f"• ¡Escanea el QR y listo!"
        )
        print(f"✅ Nuevo usuario registrado: {nombre_completo} ({user_id})")
        
    except Exception as e:
        print(f"❌ Error registrando usuario: {e}")
        await update.message.reply_text("❌ Error en el registro. Intenta más tarde.")

async def registro_directo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando directo /registro"""
    user_id = str(update.effective_user.id)
    nombre = update.effective_user.first_name or "Cliente"
    await registrar_usuario(update, user_id, nombre)

async def solicitar_compra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """El cliente solicita una compra - con selección de vendedor"""
    user_id = str(update.effective_user.id)
    nombre_cliente = update.effective_user.first_name or "Cliente"
    
    try:
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
            keyboard.append([InlineKeyboardButton(
                f"👤 {vendedor['nombre']}", 
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

async def agregar_vendedor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Agrega un nuevo vendedor - SOLO ADMIN"""
    user_id = str(update.effective_user.id)
    
    if not await es_admin(user_id):
        await update.message.reply_text("❌ Solo el administrador puede usar este comando.")
        return
    
    try:
        if len(context.args) < 2:
            await update.message.reply_text("📋 **USO:** /agregarvendedor [user_id] [nombre]")
            return
        
        nuevo_vendedor_id = context.args[0]
        nombre_vendedor = context.args[1].replace('_', ' ')
        
        # Verificar duplicados
        try:
            todos_datos = sheet_vendedores.get_all_values()
            if len(todos_datos) > 1:
                datos_vendedores = todos_datos[1:]
                for fila in datos_vendedores:
                    if len(fila) > 0 and str(fila[0]) == nuevo_vendedor_id:
                        estado = fila[3] if len(fila) > 3 else 'SI'
                        if estado.upper() == 'SI':
                            await update.message.reply_text(f"❌ El usuario {nuevo_vendedor_id} ya es vendedor activo.")
                            return
        except Exception as e:
            print(f"⚠️ Error verificando duplicados: {e}")
        
        sheet_vendedores.append_row([
            nuevo_vendedor_id,
            nombre_vendedor,
            datetime.now().strftime("%Y-%m-%d"),
            "SI"
        ])
        
        # Actualizar cache
        global vendedores_cache
        nuevo_vendedor_data = {
            'user_id': str(nuevo_vendedor_id),
            'nombre': nombre_vendedor
        }
        
        if vendedores_cache['data']:
            vendedores_cache['data'].append(nuevo_vendedor_data)
        else:
            vendedores_cache['data'] = [nuevo_vendedor_data]
        
        vendedores_cache['timestamp'] = datetime.now()
        
        vendedores_actual = await obtener_vendedores_activos()
        
        await update.message.reply_text(
            f"✅ **Vendedor agregado exitosamente**\n\n"
            f"👤 **Nombre:** {nombre_vendedor}\n"
            f"🆔 **ID:** {nuevo_vendedor_id}\n"
            f"👥 **Total vendedores:** {len(vendedores_actual)}"
        )
        print(f"✅ Vendedor agregado: {nombre_vendedor} ({nuevo_vendedor_id})")
        
    except Exception as e:
        print(f"❌ Error agregando vendedor: {e}")
        await update.message.reply_text("❌ Error agregando vendedor.")

async def eliminar_vendedor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Elimina un vendedor - SOLO ADMIN"""
    user_id = str(update.effective_user.id)
    
    if not await es_admin(user_id):
        await update.message.reply_text("❌ Solo el administrador puede usar este comando.")
        return
    
    try:
        if not context.args:
            await update.message.reply_text("📋 **USO:** /eliminarvendedor [user_id]")
            return
        
        vendedor_id = context.args[0]
        
        if vendedor_id == ADMIN_ID:
            await update.message.reply_text("❌ No puedes eliminarte a ti mismo como admin.")
            return
        
        # Buscar y desactivar vendedor
        todos_datos = sheet_vendedores.get_all_values()
        datos_vendedores = todos_datos[1:]
        
        vendedor_encontrado = False
        for i, fila in enumerate(datos_vendedores, start=2):
            if len(fila) > 0 and str(fila[0]) == vendedor_id:
                sheet_vendedores.update_cell(i, 4, "NO")
                vendedor_encontrado = True
                break
        
        if not vendedor_encontrado:
            await update.message.reply_text("❌ Vendedor no encontrado.")
            return
        
        # Actualizar cache
        global vendedores_cache
        if vendedores_cache['data']:
            vendedores_cache['data'] = [v for v in vendedores_cache['data'] if v['user_id'] != vendedor_id]
            vendedores_cache['timestamp'] = datetime.now()
        
        await update.message.reply_text(f"✅ **Vendedor eliminado exitosamente**\n\n🆔 **ID:** {vendedor_id}")
        print(f"✅ Vendedor eliminado: {vendedor_id}")
        
    except Exception as e:
        print(f"❌ Error eliminando vendedor: {e}")
        await update.message.reply_text("❌ Error eliminando vendedor.")

async def listar_vendedores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista todos los vendedores - SOLO ADMIN - CORREGIDO"""
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
                es_admin_str = " 👑(Admin)" if vendedor['user_id'] == ADMIN_ID else ""
                # ✅ CORRECCIÓN: Eliminé los backticks que causaban el error
                mensaje += f"{i}. {vendedor['nombre']} (ID: {vendedor['user_id']}){es_admin_str}\n"
        
        total_general = len(vendedores)
        vendedores_normales = [v for v in vendedores if v['user_id'] != ADMIN_ID]
        total_eliminables = len(vendedores_normales)
        
        mensaje += f"\n📊 **Total en sistema:** {total_general} vendedores"
        if total_general > total_eliminables:
            mensaje += f"\n👑 **Eres el admin** (no puedes eliminarte)"
        if total_eliminables > 0:
            mensaje += f"\n🚫 **Disponibles para eliminar:** {total_eliminables} vendedores"
        
        # ✅ CORRECCIÓN: Sin parse_mode para evitar errores
        await update.message.reply_text(mensaje)
        
    except Exception as e:
        print(f"❌ Error listando vendedores: {e}")
        await update.message.reply_text("❌ Error listando vendedores.")

async def clientes_vendedor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra últimos clientes atendidos"""
    user_id = str(update.effective_user.id)
    
    if not await es_vendedor(user_id) and not await es_admin(user_id):
        await update.message.reply_text("❌ Solo vendedores y administradores pueden usar este comando.")
        return
    
    try:
        if not sheet_registro:
            await update.message.reply_text("❌ Error de conexión con Google Sheets.")
            return
        
        todos_datos = sheet_registro.get_all_values()
        
        if len(todos_datos) <= 1:
            await update.message.reply_text("👥 **ÚLTIMOS CLIENTES ATENDIDOS**\n\n📭 No hay clientes registrados aún.")
            return
        
        headers = todos_datos[0]
        datos_clientes = todos_datos[1:]
        
        if not datos_clientes:
            await update.message.reply_text("👥 **ÚLTIMOS CLIENTES ATENDIDOS**\n\n📭 No hay clientes registrados aún.")
            return
        
        ultimos_clientes = datos_clientes[-5:] if len(datos_clientes) >= 5 else datos_clientes
        ultimos_clientes.reverse()
        
        mensaje = "👥 **ÚLTIMOS CLIENTES REGISTRADOS**\n\n"
        
        for i, cliente in enumerate(ultimos_clientes, 1):
            if len(cliente) >= 2:
                user_id_cliente = cliente[0]
                username = cliente[1] if len(cliente) > 1 else "Sin username"
                fecha = cliente[2] if len(cliente) > 2 else "Fecha no registrada"
                
                nombre_mostrar = username if username and username != "Sin username" else f"Usuario {user_id_cliente}"
                mensaje += f"{i}. {nombre_mostrar} - {fecha}\n"
        
        total_clientes = len(datos_clientes)
        mensaje += f"\n📊 **Total clientes registrados:** {total_clientes}"
        
        await update.message.reply_text(mensaje)
        print(f"📋 {user_id} consultó lista de clientes")
        
    except Exception as e:
        print(f"❌ Error en comando clientes: {e}")
        await update.message.reply_text("❌ Error obteniendo datos de clientes.")

async def compras_vendedor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra estadísticas del vendedor"""
    user_id = str(update.effective_user.id)
    
    if not await es_vendedor(user_id) and not await es_admin(user_id):
        await update.message.reply_text("❌ Solo vendedores y administradores pueden usar este comando.")
        return
    
    try:
        if not sheet_registro:
            await update.message.reply_text("❌ Error de conexión con Google Sheets.")
            return
        
        todos_datos = sheet_registro.get_all_values()
        
        if len(todos_datos) <= 1:
            await update.message.reply_text("📈 **MIS ESTADÍSTICAS**\n\n📭 No hay datos de ventas aún.")
            return
        
        headers = todos_datos[0]
        datos_clientes = todos_datos[1:]
        
        total_clientes = len(datos_clientes)
        
        total_sellos = 0
        for cliente in datos_clientes:
            if len(cliente) > 3 and cliente[3]:
                try:
                    total_sellos += int(cliente[3])
                except:
                    pass
        
        if await es_admin(user_id):
            mensaje = (
                f"📈 **ESTADÍSTICAS GENERALES - ADMIN**\n\n"
                f"👥 **Total clientes registrados:** {total_clientes}\n"
                f"🏷️ **Total ventas registradas:** {total_sellos}\n"
                f"💰 **Estimado ingresos:** ${total_sellos * 50000:,}\n\n"
                f"💡 *Basado en 50,000 por arguile*"
            )
        else:
            clientes_vendedor = 0
            ventas_vendedor = 0
            
            for cliente in datos_clientes:
                if len(cliente) > 4 and str(cliente[4]) == user_id:
                    clientes_vendedor += 1
                    if len(cliente) > 3 and cliente[3]:
                        try:
                            ventas_vendedor += int(cliente[3])
                        except:
                            pass
            
            mensaje = (
                f"📈 **MIS ESTADÍSTICAS**\n\n"
                f"🛒 **Ventas registradas:** {ventas_vendedor}\n"
                f"👥 **Mis clientes:** {clientes_vendedor}\n"
                f"💰 **Mis ventas estimadas:** ${ventas_vendedor * 50000:,}\n\n"
                f"📊 **Totales generales:**\n"
                f"• Clientes: {total_clientes}\n"
                f"• Ventas: {total_sellos}\n\n"
                f"💡 *Basado en 50,000 por arguile*"
            )
        
        await update.message.reply_text(mensaje)
        print(f"📊 {user_id} consultó estadísticas")
        
    except Exception as e:
        print(f"❌ Error en comando compras vendedor: {e}")
        await update.message.reply_text("❌ Error obteniendo estadísticas.")

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

async def procesar_compra_qr(update: Update, user_id: str, codigo_qr: str):
    """Procesa una compra desde QR único - CON NOTIFICACIÓN AL VENDEDOR"""
    try:
        if not sheet_registro:
            await update.message.reply_text("❌ Error del sistema.")
            return
        
        limpiados = limpiar_codigos_expirados()
        if limpiados > 0:
            print(f"🧹 Se limpiaron {limpiados} códigos expirados")
            
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
                
                sheet_registro.append_row([
                    user_id,
                    username,
                    datetime.now().strftime("%Y-%m-%d"),
                    1,
                    vendedor_actual
                ])
                mensaje_bienvenida = f"🎉 **¡Bienvenido {nombre_cliente}!**\nTe hemos registrado automáticamente.\n\n"
                sellos_actual = 1
            else:
                fila = celda.row
                datos_actuales = sheet_registro.row_values(fila)
                
                while len(datos_actuales) < 5:
                    datos_actuales.append("")
                
                sellos_actual = int(datos_actuales[3]) if datos_actuales[3] else 0
                nuevos_sellos = sellos_actual + 1
                
                sheet_registro.update_cell(fila, 4, nuevos_sellos)
                sheet_registro.update_cell(fila, 5, vendedor_actual)
                mensaje_bienvenida = ""
                sellos_actual = nuevos_sellos
            
            # Guardar en historial
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
            
            # ✅ NOTIFICACIÓN AL VENDEDOR
            try:
                if vendedor_actual != "todos los vendedores" and vendedor_actual != "vendedor_desconocido":
                    mensaje_vendedor = (
                        f"✅ VENTA CONFIRMADA\n\n"
                        f"👤 Cliente: {nombre_cliente}\n"
                        f"📱 ID: {user_id}\n"
                        f"🏺 Sello sumado: +1\n"
                        f"📊 Total acumulado: {sellos_actual}/10 sellos\n"
                        f"⏰ Hora: {datetime.now().strftime('%H:%M:%S')}\n\n"
                        f"¡Venta registrada exitosamente! 🎉"
                    )
                    
                    # Buscar el ID del vendedor
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
                    f"{mensaje_bienvenida}"
                    "🎉 **¡FELICIDADES!** 🎉\n\n"
                    "🏺 **Has completado 10 compras en Shisha MGTA**\n\n"
                    "💰 **PREMIO:** 50% DE DESCUENTO\n"
                    "en tu próxima compra de arguile\n\n"
                    "📱 Muestra este mensaje al hacer tu pedido\n"
                    "¡Gracias por tu preferencia!"
                )
                print(f"🎉 Usuario {user_id} ganó 50% descuento")
            else:
                await update.message.reply_text(
                    f"{mensaje_bienvenida}"
                    f"✅ **Compra registrada exitosamente**\n\n"
                    f"🏺 Shisha MGTA agradece tu compra\n\n"
                    f"📊 **Sellos acumulados:** {sellos_actual}/10\n"
                    f"🎯 **Te faltan:** {10 - sellos_actual}\n\n"
                    f"¡Sigue disfrutando de nuestros arguiles de calidad!"
                )
            
            del codigos_activos[codigo_qr]
            print(f"✅ Compra registrada via QR para usuario {user_id} con vendedor {vendedor_actual}")
            
        else:
            await update.message.reply_text("❌ QR inválido o ya utilizado.")
            
    except Exception as e:
        print(f"❌ Error procesando QR: {e}")
        await update.message.reply_text("❌ Error procesando compra.")

async def sellos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra los sellos actuales del usuario - MENSAJE MEJORADO"""
    user_id = str(update.effective_user.id)
    
    try:
        if not sheet_registro:
            await update.message.reply_text("❌ Error del sistema.")
            return
            
        celda = sheet_registro.find(user_id)
        if celda:
            datos = sheet_registro.row_values(celda.row)
            sellos_actual = int(datos[3]) if len(datos) > 3 and datos[3] else 0
            
            # ✅ MENSAJE MEJORADO
            await update.message.reply_text(
                f"📊 Tu progreso en Shisha MGTA\n\n"
                f"🏺 Sellos acumulados: {sellos_actual}/10\n"
                f"🎯 Te faltan {10 - sellos_actual} sellos para tu 50% de descuento\n\n"
                f"¡Sigue comprando nuestros arguiles!"
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

async def ranking_vendedores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra ranking de vendedores basado en historial - DISEÑO MEJORADO"""
    user_id = str(update.effective_user.id)
    
    if not await es_admin(user_id):
        await update.message.reply_text("❌ Solo administradores pueden ver rankings.")
        return
    
    try:
        if not sheet_historial:
            await update.message.reply_text("❌ Sistema de historial no disponible.")
            return
            
        todos_datos = sheet_historial.get_all_values()
        
        if len(todos_datos) <= 1:
            await update.message.reply_text("📭 No hay datos de ventas aún.")
            return
        
        headers = todos_datos[0]
        datos_historial = todos_datos[1:]
        
        stats_vendedores = {}
        
        for fila in datos_historial:
            if len(fila) > 2:
                vendedor = fila[2]
                if vendedor and vendedor != "vendedor_desconocido":
                    if vendedor not in stats_vendedores:
                        stats_vendedores[vendedor] = {
                            'ventas': 0,
                            'clientes_unicos': set()
                        }
                    
                    stats_vendedores[vendedor]['ventas'] += 1
                    if len(fila) > 0 and fila[0]:
                        stats_vendedores[vendedor]['clientes_unicos'].add(fila[0])
        
        if not stats_vendedores:
            await update.message.reply_text("📭 No hay datos de ventas por vendedores.")
            return
        
        # 🎯 NUEVO RANKING - SOLO VENTAS
        ranking_ventas = sorted(stats_vendedores.items(), key=lambda x: x[1]['ventas'], reverse=True)
        
        mensaje = "🏆 **TOP VENDEDORES**\n\n"
        
        # Emojis para el podio
        emojis_podio = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        
        for i, (vendedor, stats) in enumerate(ranking_ventas[:5]):
            emoji = emojis_podio[i] if i < len(emojis_podio) else f"{i+1}️⃣"
            ventas = stats['ventas']
            clientes_unicos = len(stats['clientes_unicos'])
            
            mensaje += f"{emoji} **{vendedor}**\n"
            mensaje += f"   📦 {ventas} venta{'s' if ventas > 1 else ''}\n"
            mensaje += f"   👥 {clientes_unicos} cliente{'s' if clientes_unicos > 1 else ''}\n\n"
        
        # Totales generales
        total_ventas = sum(stats['ventas'] for stats in stats_vendedores.values())
        total_vendedores = len(stats_vendedores)
        
        mensaje += f"📊 **RESUMEN GENERAL**\n"
        mensaje += f"• Total ventas: **{total_ventas}**\n"
        mensaje += f"• Vendedores activos: **{total_vendedores}**\n"
        mensaje += f"• Período: Últimas ventas registradas"
        
        await update.message.reply_text(mensaje, parse_mode='Markdown')
        print(f"🏆 {user_id} consultó ranking de vendedores")
        
    except Exception as e:
        print(f"❌ Error en ranking: {e}")
        await update.message.reply_text("❌ Error generando ranking.")

async def obtener_estadisticas_actualizadas():
    """Obtiene estadísticas actualizadas del sistema"""
    try:
        if not sheet_registro:
            return "❌ Error de conexión con Google Sheets"
        
        todos_los_datos = sheet_registro.get_all_values()
        
        if len(todos_los_datos) <= 1:
            return "📊 **ESTADÍSTICAS**\n\nNo hay registros aún."
        
        headers = todos_los_datos[0]
        datos_filas = todos_los_datos[1:]
        
        datos_dict = []
        for fila in datos_filas:
            if len(fila) >= len(headers):
                fila_dict = {}
                for i, header in enumerate(headers):
                    if i < len(fila):
                        fila_dict[header] = fila[i]
                    else:
                        fila_dict[header] = ""
                datos_dict.append(fila_dict)
        
        if not datos_dict:
            return "📊 **ESTADÍSTICAS**\n\nNo hay registros válidos."
        
        df = pd.DataFrame(datos_dict)
        
        try:
            df['sellos'] = pd.to_numeric(df['sellos'], errors='coerce').fillna(0).astype(int)
            df['fecha_registro'] = pd.to_datetime(df['fecha_registro'], errors='coerce')
        except Exception as e:
            print(f"⚠️ Error convirtiendo tipos de datos: {e}")
        
        total_registros = len(df)
        usuarios_unicos = df['user_id'].nunique()
        total_sellos = df['sellos'].sum()
        
        fechas_validas = df['fecha_registro'].dropna()
        if not fechas_validas.empty:
            fecha_min = fechas_validas.min().strftime('%Y-%m-%d')
            fecha_max = fechas_validas.max().strftime('%Y-%m-%d')
        else:
            fecha_min = "N/A"
            fecha_max = "N/A"
        
        registros_por_dia = df.groupby(df['fecha_registro'].dt.date).size()
        
        stats_vendedores = ""
        if 'vendedor_asignado' in df.columns:
            vendedores_stats = df.groupby('vendedor_asignado').agg({
                'user_id': 'count',
                'sellos': 'sum'
            }).rename(columns={'user_id': 'clientes'})
            
            vendedores_stats = vendedores_stats[vendedores_stats.index.notna() & (vendedores_stats.index != "")]
            
            if not vendedores_stats.empty:
                stats_vendedores = "\n👥 **RANKING VENDEDORES:**\n"
                for vendedor, stats in vendedores_stats.sort_values('clientes', ascending=False).iterrows():
                    stats_vendedores += f"   {vendedor}: {int(stats['clientes'])} clientes, {int(stats['sellos'])} sellos\n"
        
        estadisticas = f"""
📊 **ESTADÍSTICAS PROMO SELLOS**

• 📈 Total registros: {total_registros}
• 👥 Usuarios únicos: {usuarios_unicos}
• 🏷️ Total sellos distribuidos: {int(total_sellos)}
• 📅 Periodo: {fecha_min} a {fecha_max}

📅 **REGISTROS POR DÍA:**
"""
        for fecha, cantidad in registros_por_dia.items():
            if pd.notna(fecha):
                estadisticas += f"   {fecha}: {cantidad} registros\n"
        
        estadisticas += stats_vendedores
        
        return estadisticas
        
    except Exception as e:
        return f"❌ Error obteniendo estadísticas: {str(e)}"

# HANDLERS PRINCIPALES
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('registro', registro_directo))
    app.add_handler(CommandHandler('compras', solicitar_compra))
    app.add_handler(CallbackQueryHandler(manejar_seleccion_vendedor, pattern='^vendedor_'))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_botones_avanzados))
    
    app.add_handler(CallbackQueryHandler(manejar_eliminar_vendedor, pattern='^eliminar_'))
    
    app.add_handler(CommandHandler('agregarvendedor', agregar_vendedor))
    app.add_handler(CommandHandler('eliminarvendedor', eliminar_vendedor))
    app.add_handler(CommandHandler('listarvendedores', listar_vendedores))
    
    app.add_handler(CommandHandler('clientes', clientes_vendedor))
    app.add_handler(CommandHandler('compras_vendedor', compras_vendedor))
    
    app.add_handler(CommandHandler('sellos', sellos))
    app.add_handler(CommandHandler('estado', sellos))
    app.add_handler(CommandHandler('info', info))
    
    app.add_handler(CommandHandler('historial', historial_cliente))
    app.add_handler(CommandHandler('ranking', ranking_vendedores))
    
    print("🚀 Shisha MGTA Bot - INICIADO")
    print("✅ FUNCIONALIDADES ACTIVAS:")
    print("   • 📊 Mensaje de progreso mejorado")
    print("   • 🔔 Notificación al vendedor después del escaneo")
    print("   • 📋 Historial de compras")
    print("   • 🏆 Ranking de vendedores (NUEVO DISEÑO)")
    print("   • 👑 Panel admin completo")
    print("   • 🎯 1 fila de headers en Vendedores")
    print("   • ✅ ERROR DE LISTAR VENDEDORES CORREGIDO")
    print("📊 Conectado a Google Sheets")
    print("🏺 Sistema de fidelidad activo")
    print("📱 QR únicos habilitados")
    print("⚡ Botones rápidos funcionando")
    print("☁️ Listo para hosting 24/7")
    print("─" * 50)
    
    app.run_polling()