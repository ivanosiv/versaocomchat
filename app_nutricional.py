import streamlit as st
import sqlite3
import os
import dotenv
import base64
from PIL import Image
from io import BytesIO
import google.generativeai as genai
import streamlit.components.v1 as components

dotenv.load_dotenv()

# --- Funções de manipulação de imagens ---
def get_image_base64(image_raw):
    buffered = BytesIO()
    image_raw.save(buffered, format=image_raw.format)
    img_byte = buffered.getvalue()
    return base64.b64encode(img_byte).decode('utf-8')

def base64_to_image(base64_string):
    base64_string = base64_string.split(",")[1]
    return Image.open(BytesIO(base64.b64decode(base64_string)))

# --- Banco de Dados ---
DB_PATH = "app.db"

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    # Tabela de usuários com dados de saúde
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            api_key TEXT,
            idade INTEGER,
            peso REAL,
            altura REAL,
            nivel_atividade TEXT,
            restricoes_alimentares TEXT
        )
    """)
    # Tabela de sessões de chat
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT DEFAULT 'Novo Chat',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    # Tabela de conversas com referência à sessão de chat
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(chat_id) REFERENCES chat_sessions(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    update_schema(conn)
    return conn

def update_schema(conn):
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(conversations)")
    columns = [col[1] for col in cursor.fetchall()]
    if "chat_id" not in columns:
        cursor.execute("ALTER TABLE conversations ADD COLUMN chat_id INTEGER")
        conn.commit()

conn = init_db()

# Função para cadastrar usuário (incluindo dados de saúde)
def register_user(username, password, api_key, idade, peso, altura, nivel_atividade, restricoes_alimentares):
    cursor = conn.cursor()
    restricoes_str = ",".join(restricoes_alimentares) if restricoes_alimentares else ""
    try:
        cursor.execute(
            "INSERT INTO users (username, password, api_key, idade, peso, altura, nivel_atividade, restricoes_alimentares) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (username, password, api_key, idade, peso, altura, nivel_atividade, restricoes_str)
        )
        conn.commit()
        return True, "Usuário registrado com sucesso!"
    except sqlite3.IntegrityError:
        return False, "Usuário já existe."

# Atualiza os dados de saúde do usuário
def update_user_health(user_id, idade, peso, altura, nivel_atividade, restricoes_alimentares):
    cursor = conn.cursor()
    restricoes_str = ",".join(restricoes_alimentares) if restricoes_alimentares else ""
    cursor.execute(
        "UPDATE users SET idade = ?, peso = ?, altura = ?, nivel_atividade = ?, restricoes_alimentares = ? WHERE id = ?",
        (idade, peso, altura, nivel_atividade, restricoes_str, user_id)
    )
    conn.commit()

# Busca usuário (incluindo dados de saúde) para login
def login_user(username, password):
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, api_key, idade, peso, altura, nivel_atividade, restricoes_alimentares FROM users WHERE username = ? AND password = ?",
        (username, password)
    )
    result = cursor.fetchone()
    if result:
        return {
            "id": result[0],
            "username": username,
            "api_key": result[1],
            "idade": result[2],
            "peso": result[3],
            "altura": result[4],
            "nivel_atividade": result[5],
            "restricoes_alimentares": result[6]
        }
    else:
        return None

def create_chat_session(user_id, title="Novo Chat"):
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_sessions (user_id, title) VALUES (?, ?)", (user_id, title))
    conn.commit()
    return cursor.lastrowid

def get_chat_sessions(user_id):
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, timestamp FROM chat_sessions WHERE user_id = ? ORDER BY timestamp DESC", (user_id,))
    return cursor.fetchall()

def get_conversation_history(chat_id):
    cursor = conn.cursor()
    cursor.execute("SELECT role, content, timestamp FROM conversations WHERE chat_id = ? ORDER BY timestamp", (chat_id,))
    rows = cursor.fetchall()
    history = []
    for role, content, timestamp in rows:
        history.append({"role": role, "content": content, "timestamp": timestamp})
    return history

def add_message(chat_id, user_id, role, content):
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO conversations (chat_id, user_id, role, content) VALUES (?, ?, ?, ?)",
        (chat_id, user_id, role, content)
    )
    conn.commit()

# --- Conversão de mensagens para formato Gemini ---
def messages_to_gemini(messages):
    gemini_messages = []
    prev_role = None
    for message in messages:
        if prev_role and (prev_role == message["role"]):
            gemini_message = gemini_messages[-1]
        else:
            gemini_message = {
                "role": "model" if message["role"] == "assistant" else "user",
                "parts": [],
            }
        for content in message["content"]:
            if content["type"] == "text":
                gemini_message["parts"].append(content["text"])
            elif content["type"] == "image_url":
                gemini_message["parts"].append(base64_to_image(content["image_url"]["url"]))
        if prev_role != message["role"]:
            gemini_messages.append(gemini_message)
        prev_role = message["role"]
    return gemini_messages

# --- Função para transmitir a resposta do modelo (texto) ---
def stream_llm_response(model_params, api_key=None, prompt_override=None):
    response_message = ""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=model_params["model"],
        generation_config={"temperature": model_params["temperature"]}
    )
    if prompt_override:
        gemini_messages = [{"role": "user", "parts": [prompt_override]}]
    else:
        gemini_messages = messages_to_gemini(st.session_state.messages)
    for chunk in model.generate_content(contents=gemini_messages, stream=True):
        chunk_text = chunk.text or ""
        response_message += chunk_text
        yield chunk_text
    st.session_state.messages.append({"role": "assistant", "content": [{"type": "text", "text": response_message}]})
    add_message(st.session_state.chat_id, st.session_state.user["id"], "assistant", response_message)

# --- Nova Função: Streaming Multimídia Realtime ---
def stream_multimedia_realtime_response(api_key, prompt_override=None):
    """
    Utiliza o modelo gemini-2.0-flash-exp para gerar respostas realtime que possam conter mídia:
    texto, áudio, vídeo e imagem.
    Cada chunk é verificado: se contiver texto, ele é enviado normalmente; se contiver dados inline,
    o MIME type é verificado e uma mensagem resumida é gerada.
    """
    response_message = ""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash-exp",
        generation_config={"temperature": 0.3}
    )
    if prompt_override:
        gemini_messages = [{"role": "user", "parts": [prompt_override]}]
    else:
        gemini_messages = messages_to_gemini(st.session_state.messages)
    for chunk in model.generate_content(contents=gemini_messages, stream=True):
        if chunk.text:
            response_message += chunk.text
            yield chunk.text
        elif hasattr(chunk, 'inline_data') and chunk.inline_data is not None:
            mime = chunk.inline_data.mime_type
            data_b64 = base64.b64encode(chunk.inline_data.data).decode('utf-8')
            if mime == "audio/pcm":
                msg = f"[Áudio recebido: {data_b64[:30]}...]"
            elif mime == "video/mp4":
                msg = f"[Vídeo recebido: {data_b64[:30]}...]"
            elif mime == "image/jpeg":
                msg = f"[Imagem recebida: {data_b64[:30]}...]"
            else:
                msg = f"[Mídia recebida: {data_b64[:30]}...]"
            response_message += msg
            yield msg
    st.session_state.messages.append({"role": "assistant", "content": [{"type": "text", "text": response_message}]})
    add_message(st.session_state.chat_id, st.session_state.user["id"], "assistant", response_message)

# --- Função para carregar a interface HTML do chat realtime via iframe ---
def realtime_chat_interface():
    """
    Exibe o frontend do gemini-multimodal-playground via iframe,
    com opção de alternar para o modo tela cheia.
    """
    chat_url = "http://127.0.0.1:3000/"
    st.info("Certifique-se de que o servidor do frontend esteja rodando em http://127.0.0.1:3000/")

    if "show_iframe_full" not in st.session_state:
        st.session_state.show_iframe_full = False

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("🔳 Abrir em Tela Cheia"):
            st.session_state.show_iframe_full = True

    if st.session_state.show_iframe_full:
        st.markdown("### Modo Tela Cheia")
        st.markdown(
            f"""
            <iframe src="{chat_url}"
                width="100%"
                height="900"
                frameborder="0"
                allow="microphone; camera; display-capture; autoplay"
                allowfullscreen
                style="border-radius: 10px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);">
            </iframe>
            """,
            unsafe_allow_html=True
        )
    else:
        st.markdown("### Visualização Padrão")
        components.html(
            f"""
            <iframe src="{chat_url}"
                width="100%"
                height="600"
                frameborder="0"
                allow="microphone; camera; display-capture; autoplay"
                allowfullscreen>
            </iframe>
            """,
            height=600,
            scrolling=True,
        )




# --- Funções específicas do aplicativo ---
def analyze_dish_image(image, google_api_key, idade, peso, altura, imc, nivel_atividade):
    text_prompt = (
        f"Atue como um nutricionista. Aqui estão algumas informações para ajudar a estimar as calorias do prato: "
        f"idade {idade}, peso {peso} kg, altura {altura} m, IMC {imc} e nível de atividade física {nivel_atividade}. "
        "Por favor, forneça uma estimativa calórica para este prato com base nas informações fornecidas."
    )
    image_b64 = get_image_base64(image)
    # Adiciona a imagem ao histórico
    st.session_state.messages.append({
        "role": "user",
        "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]
    })
    add_message(st.session_state.chat_id, st.session_state.user["id"], "user", "[Imagem em base64]")
    # Adiciona o prompt textual também
    st.session_state.messages.append({
        "role": "user",
        "content": [{"type": "text", "text": text_prompt}]
    })
    add_message(st.session_state.chat_id, st.session_state.user["id"], "user", text_prompt)
    with st.chat_message("assistant"):
        st.write_stream(stream_llm_response({"model": "gemini-2.0-flash", "temperature": 0.3}, google_api_key))

def recommend_recipes_with_ingredients(image, google_api_key):
    restricoes = ", ".join(st.session_state.restricoes_alimentares) if st.session_state.restricoes_alimentares else ""
    image_b64 = get_image_base64(image)
    st.session_state.messages.append({
        "role": "user",
        "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]
    })
    add_message(st.session_state.chat_id, st.session_state.user["id"], "user", "[Imagem em base64]")
    st.session_state.messages.append({
        "role": "user",
        "content": [{"type": "text", "text": f"Baseando-se nos ingredientes da imagem e nas seguintes restrições alimentares: {restricoes}, recomende receitas saudáveis para o perfil do usuário."}]
    })
    add_message(st.session_state.chat_id, st.session_state.user["id"], "user", f"Baseando-se nos ingredientes da imagem e restrições: {restricoes}")
    with st.chat_message("assistant"):
        st.write_stream(stream_llm_response({"model": "gemini-2.0-flash", "temperature": 0.3}, google_api_key))

def generate_shopping_list_recipes(shopping_list, days, google_api_key):
    user_health = f"idade {st.session_state.user.get('idade')}, peso {st.session_state.user.get('peso')} kg, altura {st.session_state.user.get('altura')} m, nível de atividade física {st.session_state.user.get('nivel_atividade')}"
    restricoes = st.session_state.user.get('restricoes_alimentares') or "Nenhuma"
    restricoes = restricoes.replace(",", ", ")
    prompt = (f"Você é um nutricionista. Tenho a seguinte lista de compras: {shopping_list}. "
              f"Preciso de receitas para os próximos {days} dias. Considere meus dados de saúde: {user_health} "
              f"e minhas restrições alimentares: {restricoes}. "
              f"Por favor, elabore uma receita balanceada para cada dia, contando somente com a minha lista de compras.")
    
    st.session_state.messages.append({
        "role": "user",
        "content": [{"type": "text", "text": f"Gerar receitas com base na lista de compras: {shopping_list} para {days} dias."}]
    })
    add_message(st.session_state.chat_id, st.session_state.user["id"], "user", f"Gerar receitas com base na lista de compras: {shopping_list} para {days} dias")
    with st.chat_message("assistant"):
        st.write_stream(stream_llm_response({"model": "gemini-2.0-flash", "temperature": 0.3}, google_api_key, prompt_override=prompt))

# --- Tela de Login e Cadastro ---
def login_screen():
    st.sidebar.title("Autenticação")
    mode = st.sidebar.radio("Entre ou Cadastre-se", ["Login", "Cadastro"])
    username = st.sidebar.text_input("Usuário")
    password = st.sidebar.text_input("Senha", type="password")
    api_key = st.sidebar.text_input("Chave API do Google (opcional no login)", type="password")
    
    if mode == "Cadastro":
        st.sidebar.write("### Dados de Saúde do Usuário")
        idade_reg = st.sidebar.number_input("Idade", min_value=1, max_value=120, step=1)
        peso_reg = st.sidebar.number_input("Peso (kg)", min_value=1.0, format="%.2f")
        altura_reg = st.sidebar.number_input("Altura (m)", min_value=0.5, format="%.2f")
        nivel_atividade_reg = st.sidebar.selectbox("Nível de Atividade Física", ["Sedentário", "Moderado", "Ativo", "Muito Ativo"])
        restricoes_alimentares_reg = st.sidebar.multiselect("Restrições Alimentares", 
                                                            ["Diabetes", "Hipertensão", "Alergias Alimentares", "Doenças Celíacas", 
                                                             "Vegetariano", "Vegano", "Low Carb", "Keto"])
        if st.sidebar.button("Cadastrar"):
            if username and password:
                success, msg = register_user(username, password, api_key, idade_reg, peso_reg, altura_reg, nivel_atividade_reg, restricoes_alimentares_reg)
                st.sidebar.info(msg)
            else:
                st.sidebar.error("Preencha usuário e senha para cadastro.")
    else:
        if st.sidebar.button("Login"):
            user = login_user(username, password)
            if user:
                st.session_state.user = user
                chat_id = create_chat_session(user["id"])
                st.session_state.chat_id = chat_id
                st.session_state.messages = get_conversation_history(chat_id)
                st.sidebar.success(f"Bem-vindo, {username}!")
            else:
                st.sidebar.error("Usuário ou senha incorretos.")

# --- Função principal ---
def main():
    st.set_page_config(page_title="App Nutrição", page_icon="🤖", layout="centered", initial_sidebar_state="expanded")
    
    if "user" not in st.session_state:
        login_screen()
        st.write("Por favor, realize o login ou cadastro para utilizar o aplicativo.")
        return

    # Inicializa restricoes_alimentares no session_state, se não existir
    if "restricoes_alimentares" not in st.session_state:
        restricoes = st.session_state.user.get("restricoes_alimentares", "")
        st.session_state.restricoes_alimentares = [r.strip() for r in restricoes.split(",")] if restricoes else []
    
    st.title("App Nutrição 💬")
    
    menu_option = st.sidebar.radio("Opções", ["Chat", "Histórico de Conversas", "Novo Chat"])
    
    with st.sidebar.expander("Alterar Dados de Saúde", expanded=False):
        current_idade = st.session_state.user.get("idade") or 25
        current_peso = st.session_state.user.get("peso") or 70.0
        current_altura = st.session_state.user.get("altura") or 1.75
        current_nivel = st.session_state.user.get("nivel_atividade") or "Moderado"
        current_restricoes = st.session_state.restricoes_alimentares
        new_idade = st.number_input("Idade", min_value=1, max_value=120, step=1, value=current_idade, key="upd_idade")
        new_peso = st.number_input("Peso (kg)", min_value=1.0, format="%.2f", value=current_peso, key="upd_peso")
        new_altura = st.number_input("Altura (m)", min_value=0.5, format="%.2f", value=current_altura, key="upd_altura")
        new_nivel = st.selectbox("Nível de Atividade Física", ["Sedentário", "Moderado", "Ativo", "Muito Ativo"],
                                 index=["Sedentário", "Moderado", "Ativo", "Muito Ativo"].index(current_nivel), key="upd_nivel")
        new_restricoes = st.multiselect("Restrições Alimentares", 
                                        ["Diabetes", "Hipertensão", "Alergias Alimentares", "Doenças Celíacas", 
                                         "Vegetariano", "Vegano", "Low Carb", "Keto"], default=current_restricoes, key="upd_restricoes")
        if st.button("Atualizar Dados de Saúde"):
            update_user_health(st.session_state.user["id"], new_idade, new_peso, new_altura, new_nivel, new_restricoes)
            st.session_state.user["idade"] = new_idade
            st.session_state.user["peso"] = new_peso
            st.session_state.user["altura"] = new_altura
            st.session_state.user["nivel_atividade"] = new_nivel
            st.session_state.user["restricoes_alimentares"] = ",".join(new_restricoes)
            st.session_state.restricoes_alimentares = new_restricoes
            st.success("Dados de saúde atualizados com sucesso!")
    
    google_api_key = st.text_input("Sua chave API do Google", value=st.session_state.user.get("api_key") or "", type="password")
    st.session_state.user["api_key"] = google_api_key

    st.sidebar.divider()
    st.sidebar.write("### **Dados de Saúde (Cadastro)**")
    st.sidebar.write(f"**Idade:** {st.session_state.user.get('idade', 'N/D')}")
    st.sidebar.write(f"**Peso (kg):** {st.session_state.user.get('peso', 'N/D')}")
    st.sidebar.write(f"**Altura (m):** {st.session_state.user.get('altura', 'N/D')}")
    st.sidebar.write(f"**Nível de Atividade:** {st.session_state.user.get('nivel_atividade', 'N/D')}")
    restricoes_disp = st.session_state.restricoes_alimentares
    st.sidebar.write(f"**Restrições Alimentares:** {', '.join(restricoes_disp) if restricoes_disp else 'Nenhuma'}")
    
    st.sidebar.divider()
    st.sidebar.write("### **Opções de Análise**")
    uploaded_image = st.sidebar.file_uploader("Carregar uma imagem de refeição ou ingredientes:", type=["png", "jpg", "jpeg"])
    option = st.sidebar.selectbox("Escolha a análise desejada", 
                                  ["Calcular Calorias do Prato", "Recomendar Receitas com Ingredientes", "Lista de Compras", "Chat Multimídia (Real-time)"])
    
    if option == "Lista de Compras":
        st.sidebar.subheader("Lista de Compras")
        shopping_list = st.sidebar.text_area("Informe sua lista de compras (itens separados por vírgula ou linha):")
        days = st.sidebar.number_input("Para quantos dias será a lista?", min_value=1, step=1)
        if st.sidebar.button("Gerar Receitas para os Dias"):
            generate_shopping_list_recipes(shopping_list, days, google_api_key)
            return

    if st.sidebar.button("🗑️ Resetar conversa"):
        st.session_state.messages = []
        cursor = conn.cursor()
        cursor.execute("DELETE FROM conversations WHERE chat_id = ?", (st.session_state.chat_id,))
        conn.commit()

    if menu_option == "Histórico de Conversas":
        st.subheader("Histórico de Conversas")
        sessions = get_chat_sessions(st.session_state.user["id"])
        if sessions:
            for chat in sessions:
                st.write(f"**Chat ID {chat[0]} - {chat[1]}** (Criado em {chat[2]})")
                history = get_conversation_history(chat[0])
                for message in history:
                    st.write(f"**{message['timestamp']} - {message['role'].capitalize()}:** {message['content']}")
                st.write("---")
        else:
            st.write("Nenhuma conversa encontrada.")
        return
    elif menu_option == "Novo Chat":
        new_chat_id = create_chat_session(st.session_state.user["id"])
        st.session_state.chat_id = new_chat_id
        st.session_state.messages = []
        st.success("Novo chat criado com sucesso!")
        st.write("Inicie sua nova conversa...")
        return

    # Se o usuário escolher "Chat Multimídia (Real-time)", exibe o iframe com a página do frontend
    if option == "Chat Multimídia (Real-time)":
        st.subheader("Chat Multimídia (Real-time)")
        st.info("Certifique-se de que o servidor do frontend esteja rodando em http://127.0.0.1:3000/")
        realtime_chat_interface()
    else:
        st.subheader("Conversa")
        if uploaded_image:
            image = Image.open(uploaded_image)
            if option == "Calcular Calorias do Prato":
                imc = round(st.session_state.user.get("peso", 70) / (st.session_state.user.get("altura", 1.75) ** 2), 2) if st.session_state.user.get("altura") else 0
                analyze_dish_image(image, google_api_key, st.session_state.user.get("idade"), st.session_state.user.get("peso"), st.session_state.user.get("altura"), imc, st.session_state.user.get("nivel_atividade"))
            elif option == "Recomendar Receitas com Ingredientes":
                recommend_recipes_with_ingredients(image, google_api_key)

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                for content in message["content"]:
                    if content["type"] == "text":
                        st.write(content["text"])
                    elif content["type"] == "image_url":
                        st.image(content["image_url"]["url"])

        if prompt := st.chat_input("Digite uma pergunta ou pedido de recomendação..."):
            st.session_state.messages.append({"role": "user", "content": [{"type": "text", "text": prompt}]})
            add_message(st.session_state.chat_id, st.session_state.user["id"], "user", prompt)
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                st.write_stream(stream_llm_response({"model": "gemini-2.0-flash", "temperature": 0.3}, google_api_key, prompt_override=prompt))

if __name__ == "__main__":
    main()
