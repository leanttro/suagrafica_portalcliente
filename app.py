import os
import json
import uuid
import hashlib
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
import traceback
# --- NOVO IMPORT PARA O CHATBOT ---
import google.generativeai as genai

# ======================================================================
# API BACKEND - [SUA GRÁFICA] B2B PORTAL
# Versão: 1.6 (Integração do Chatbot EloBot sem alterar funções legadas)
# ======================================================================

load_dotenv()
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}}) 

# 💡 ATENÇÃO: Verifique se sua variável de ambiente DATABASE_URL está configurada
DATABASE_URL = os.environ.get("DATABASE_URL") 
ADMIN_SESSIONS = {}

# --- CONFIGURAÇÃO GEMINI (CHATBOT) ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("🔴 ERRO: GEMINI_API_KEY não encontrada. O Chatbot não funcionará.")
else:
    genai.configure(api_key=GEMINI_API_KEY)
    print("✅ [IA] Gemini configurado com sucesso.")


def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"🔴 ERRO AO CONECTAR NO DB: {e}")
        return None

# ======================================================================
# 1. SETUP (TABELAS)
# ======================================================================
def setup_database():
    conn = get_db_connection()
    if not conn: return
    
    SQL_COMMANDS = [
        """
        CREATE TABLE IF NOT EXISTS suagrafica_admin (
            id SERIAL PRIMARY KEY,
            username VARCHAR(255) UNIQUE NOT NULL,
            chave_admin VARCHAR(256) NOT NULL,
            data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS suagrafica_clientes (
            id SERIAL PRIMARY KEY,
            admin_id INTEGER REFERENCES suagrafica_admin(id) ON DELETE SET NULL,
            nome_cliente VARCHAR(255) NOT NULL,
            cnpj VARCHAR(18) UNIQUE,
            email_contato VARCHAR(255),
            codigo_acesso VARCHAR(50) UNIQUE NOT NULL,
            status_acesso VARCHAR(20) DEFAULT 'Ativo'
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS suagrafica_produtos (
            id SERIAL PRIMARY KEY,
            codigo_produto VARCHAR(50) UNIQUE NOT NULL,
            nome_produto VARCHAR(255) NOT NULL,
            descricao TEXT,
            preco_minimo DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
            multiplos_de INTEGER DEFAULT 1,
            estoque_disponivel BOOLEAN DEFAULT TRUE,
            imagem_url VARCHAR(255),
            esta_ativo BOOLEAN DEFAULT TRUE
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS suagrafica_pedidos (
            id SERIAL PRIMARY KEY,
            cliente_id INTEGER REFERENCES suagrafica_clientes(id) ON DELETE RESTRICT,
            valor_total DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
            status_pedido VARCHAR(50) DEFAULT 'Aguardando Aprovação',
            link_pagamento VARCHAR(255),
            path_comprovante VARCHAR(255),
            data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS suagrafica_pedido_itens (
            id SERIAL PRIMARY KEY,
            pedido_id INTEGER REFERENCES suagrafica_pedidos(id) ON DELETE CASCADE,
            produto_id INTEGER REFERENCES suagrafica_produtos(id) ON DELETE SET NULL,
            quantidade INTEGER NOT NULL,
            preco_unitario_registrado DECIMAL(10, 2) NOT NULL
        );
        """
    ]
    
    try:
        cur = conn.cursor()
        print("ℹ️  [DB] Verificando tabelas...")
        for cmd in SQL_COMMANDS:
            cur.execute(cmd)
        conn.commit()
        print("✅ [DB] Tabelas OK.")
    except Exception as e:
        print(f"🔴 ERRO NO SETUP: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

# ======================================================================
# 2. AUTENTICAÇÃO
# ======================================================================
def check_auth(request):
    token = request.headers.get('Authorization')
    if not token: return None
    token = token.replace('Bearer ', '')
    
    # Aceita os tokens forçados para que o painel admin carregue
    if token in ['FORCED_LEANDRO_TOKEN', 'FORCED_TESTE_TOKEN']:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM suagrafica_admin LIMIT 1")
            admin = cur.fetchone()
            return admin[0] if admin else 1
        except:
            return 1 
        finally:
            if conn: conn.close()

    return ADMIN_SESSIONS.get(token)

def check_client_auth(request):
    """ Verifica a presença do token do cliente. """
    token = request.headers.get('Authorization')
    if not token: 
        return False
    return True

@app.route('/api/admin/login', methods=['POST'])
def login_admin():
    data = request.json or {}
    username = data.get('username', '').strip()
    chave_admin = data.get('chave_admin', '').strip()

    if not username or not chave_admin:
        return jsonify({"erro": "Credenciais incompletas"}), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT id, username, chave_admin 
            FROM suagrafica_admin 
            WHERE LOWER(username) = LOWER(%s)
        """, (username,))
        
        admin = cur.fetchone()
        
        if admin and admin[2] == chave_admin:
            token = str(uuid.uuid4())
            ADMIN_SESSIONS[token] = admin[0]
            return jsonify({"mensagem": "Login realizado", "token": token, "admin_id": admin[0]})
        else:
            return jsonify({"erro": "Usuário ou senha incorretos"}), 401
    finally:
        if conn: conn.close()

@app.route('/api/cliente/login', methods=['POST'])
def login_cliente():
    data = request.json or {}
    codigo_acesso = data.get('codigo_acesso', '').strip()

    if not codigo_acesso:
        return jsonify({"erro": "Código de Acesso não fornecido"}), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT id, nome_cliente, status_acesso 
            FROM suagrafica_clientes 
            WHERE codigo_acesso = %s
        """, (codigo_acesso,))
        
        cliente = cur.fetchone()
        
        if cliente:
            cliente_id, nome_cliente, status_acesso = cliente
            
            if status_acesso != 'Ativo':
                return jsonify({"erro": "Seu acesso está inativo. Contate o suporte."}), 401
                
            cliente_token = hashlib.sha256(f"{cliente_id}:{uuid.uuid4()}".encode()).hexdigest()
            
            return jsonify({
                "mensagem": "Login de Cliente realizado", 
                "token": cliente_token, 
                "cliente_id": cliente_id,
                "nome_cliente": nome_cliente
            }), 200
        else:
            return jsonify({"erro": "Código de acesso incorreto ou cliente não encontrado"}), 401
    except Exception as e:
        traceback.print_exc()
        return jsonify({"erro": f"Erro interno: {str(e)}"}), 500
    finally:
        if conn: conn.close()
        
# ======================================================================
# 3. DASHBOARD & CRUD (ADMIN)
# ======================================================================
@app.route('/api/admin/dashboard_stats', methods=['GET'])
def admin_stats():
    if not check_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM suagrafica_clientes WHERE status_acesso = 'Ativo'")
        cl = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM suagrafica_produtos WHERE esta_ativo = TRUE")
        pr = cur.fetchone()[0]
        # Conta pedidos no status 'Aguardando Aprovação' ou 'Aguardando Pagamento'
        cur.execute("SELECT COUNT(*) FROM suagrafica_pedidos WHERE status_pedido IN ('Aguardando Aprovação', 'Aguardando Pagamento')")
        pe = cur.fetchone()[0]
        return jsonify({"stat_clientes": cl, "stat_produtos": pr, "stat_pedidos": pe})
    finally:
        if conn: conn.close()

@app.route('/api/admin/produtos', methods=['GET', 'POST'])
def admin_gerenciar_produtos():
    if not check_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if request.method == 'GET':
            cur.execute("SELECT * FROM suagrafica_produtos ORDER BY nome_produto")
            produtos = cur.fetchall()
            for p in produtos: p['preco_minimo'] = float(p['preco_minimo'])
            return jsonify(produtos)
        elif request.method == 'POST':
            data = request.json or {}
            cur.execute("""
                INSERT INTO suagrafica_produtos (codigo_produto, nome_produto, preco_minimo, multiplos_de, descricao, imagem_url, esta_ativo, estoque_disponivel)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (data.get('codigo_produto'), data.get('nome_produto'), data.get('preco_minimo'), data.get('multiplos_de', 1), data.get('descricao'), data.get('imagem_url'), data.get('esta_ativo', True), data.get('estoque_disponivel', True)))
            conn.commit()
            return jsonify({"mensagem": "Produto criado!", "id": cur.fetchone()['id']}), 201
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"erro": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/admin/produtos/<int:id>', methods=['GET', 'PUT', 'DELETE'])
def admin_crud_produto_by_id(id):
    if not check_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if request.method == 'GET':
            cur.execute("SELECT * FROM suagrafica_produtos WHERE id = %s", (id,))
            p = cur.fetchone()
            if p: p['preco_minimo'] = float(p['preco_minimo'])
            return jsonify(p or {"erro": "Não encontrado"}), 200 if p else 404
        elif request.method == 'PUT':
            data = request.json or {}
            cur.execute("""
                UPDATE suagrafica_produtos SET codigo_produto=%s, nome_produto=%s, preco_minimo=%s, multiplos_de=%s, descricao=%s, imagem_url=%s, esta_ativo=%s, estoque_disponivel=%s WHERE id=%s
            """, (data.get('codigo_produto'), data.get('nome_produto'), data.get('preco_minimo'), data.get('multiplos_de'), data.get('descricao'), data.get('imagem_url'), data.get('esta_ativo'), data.get('estoque_disponivel'), id))
            conn.commit()
            return jsonify({"mensagem": "Atualizado!"})
        elif request.method == 'DELETE':
            cur.execute("DELETE FROM suagrafica_produtos WHERE id = %s", (id,))
            conn.commit()
            return jsonify({"mensagem": "Deletado!"})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"erro": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/admin/clientes', methods=['GET', 'POST'])
def admin_gerenciar_clientes():
    admin_id = check_auth(request)
    if not admin_id: return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if request.method == 'GET':
            cur.execute("SELECT * FROM suagrafica_clientes ORDER BY nome_cliente")
            return jsonify(cur.fetchall())
        elif request.method == 'POST':
            data = request.json or {}
            cur.execute("""
                INSERT INTO suagrafica_clientes (admin_id, nome_cliente, cnpj, email_contato, codigo_acesso, status_acesso)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (admin_id, data.get('nome_cliente'), data.get('cnpj'), data.get('email_contato'), data.get('codigo_acesso'), 'Ativo'))
            conn.commit()
            return jsonify({"mensagem": "Cliente criado!", "id": cur.fetchone()['id']}), 201
    except Exception as e:
        if conn: conn.rollback()
        if "unique constraint" in str(e).lower(): return jsonify({"erro": "Código/CNPJ duplicado"}), 409
        return jsonify({"erro": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/admin/clientes/<int:id>', methods=['DELETE'])
def admin_delete_cliente(id):
    if not check_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM suagrafica_clientes WHERE id = %s", (id,))
        conn.commit()
        return jsonify({"mensagem": "Cliente deletado!"})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"erro": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/admin/users', methods=['GET', 'POST'])
def admin_gerenciar_admins():
    if not check_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if request.method == 'GET':
            cur.execute("SELECT id, username, data_criacao FROM suagrafica_admin ORDER BY id")
            return jsonify(cur.fetchall())
        elif request.method == 'POST':
            data = request.json or {}
            cur.execute("INSERT INTO suagrafica_admin (username, chave_admin) VALUES (%s, %s) RETURNING id", (data.get('username'), data.get('chave_admin')))
            conn.commit()
            return jsonify({"mensagem": "Admin criado!", "id": cur.fetchone()['id']}), 201
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"erro": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/admin/users/<int:id>', methods=['DELETE'])
def admin_delete_admin(id):
    if not check_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM suagrafica_admin")
        if cur.fetchone()[0] == 1: return jsonify({"erro": "Não pode deletar o último admin"}), 400
        cur.execute("DELETE FROM suagrafica_admin WHERE id = %s", (id,))
        conn.commit()
        return jsonify({"mensagem": "Admin deletado!"})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"erro": str(e)}), 500
    finally:
        if conn: conn.close()

# Rotas de Pedidos para o Painel Admin
@app.route('/api/admin/pedidos', methods=['GET'])
def admin_listar_pedidos():
    if not check_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT p.id, c.nome_cliente, p.valor_total, p.status_pedido, p.data_criacao
            FROM suagrafica_pedidos p
            JOIN suagrafica_clientes c ON p.cliente_id = c.id
            ORDER BY p.data_criacao DESC
        """)
        pedidos = cur.fetchall()
        for p in pedidos: p['valor_total'] = float(p['valor_total'])
        return jsonify(pedidos)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/admin/pedidos/<int:id>', methods=['GET', 'PUT'])
def admin_crud_pedido_by_id(id):
    if not check_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        if request.method == 'GET':
            cur.execute("""
                SELECT p.id, c.nome_cliente, p.cliente_id, p.valor_total, p.status_pedido, p.link_pagamento, p.path_comprovante, p.data_criacao
                FROM suagrafica_pedidos p
                JOIN suagrafica_clientes c ON p.cliente_id = c.id
                WHERE p.id = %s
            """, (id,))
            pedido = cur.fetchone()
            
            if not pedido: return jsonify({"erro": "Pedido não encontrado"}), 404

            cur.execute("""
                SELECT pi.quantidade, pi.preco_unitario_registrado, pr.nome_produto, pr.codigo_produto
                FROM suagrafica_pedido_itens pi
                JOIN suagrafica_produtos pr ON pi.produto_id = pr.id
                WHERE pi.pedido_id = %s
            """, (id,))
            itens = cur.fetchall()

            pedido['valor_total'] = float(pedido['valor_total'])
            pedido['itens'] = [{'quantidade': i['quantidade'], 'preco_unitario': float(i['preco_unitario_registrado']), 'nome_produto': i['nome_produto'], 'codigo_produto': i['codigo_produto']} for i in itens]
            
            return jsonify(pedido)

        elif request.method == 'PUT':
            data = request.json or {}
            cur.execute("""
                UPDATE suagrafica_pedidos 
                SET status_pedido = %s, link_pagamento = %s, valor_total = %s 
                WHERE id = %s
            """, (data.get('status_pedido'), data.get('link_pagamento'), data.get('valor_total'), id))
            conn.commit()
            return jsonify({"mensagem": "Pedido atualizado!"})
            
    except Exception as e:
        traceback.print_exc()
        if conn: conn.rollback()
        return jsonify({"erro": str(e)}), 500
    finally:
        if conn: conn.close()


# ======================================================================
# 4. ROTAS DO CLIENTE (B2B)
# ======================================================================
@app.route('/api/cliente/produtos', methods=['GET'])
def cliente_produtos():
    if not check_client_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM suagrafica_produtos WHERE esta_ativo = TRUE AND estoque_disponivel = TRUE ORDER BY nome_produto")
        produtos = cur.fetchall()
        for p in produtos: 
            p['preco_minimo'] = float(p['preco_minimo']) 
        return jsonify(produtos)
    finally:
        if conn: conn.close()

@app.route('/api/cliente/pedidos', methods=['GET', 'POST'])
def cliente_pedidos():
    if not check_client_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # 💡 CORREÇÃO CRÍTICA: Lógica separada para GET e POST
        if request.method == 'GET':
            # Não tenta ler JSON. Apenas lê o parâmetro da URL.
            cliente_id_from_url = request.args.get('cliente_id')
            if not cliente_id_from_url:
                return jsonify({"erro": "ID do Cliente necessário para ver pedidos"}), 400
            
            try:
                # 💡 Correção do erro 500 original
                cliente_id = int(cliente_id_from_url)
            except ValueError:
                return jsonify({"erro": "ID do Cliente inválido."}), 400
            
            cur.execute("""
                SELECT id, valor_total, status_pedido, data_criacao 
                FROM suagrafica_pedidos 
                WHERE cliente_id = %s 
                ORDER BY data_criacao DESC
            """, (cliente_id,))
            
            pedidos = cur.fetchall()
            for p in pedidos: 
                p['valor_total'] = float(p['valor_total'])
            return jsonify(pedidos)
            
        elif request.method == 'POST':
            # 💡 APENAS AQUI LER O JSON
            data = request.json or {}
            
            cliente_id = data.get('cliente_id')
            itens = data.get('itens', [])
            
            if not cliente_id or not itens:
                return jsonify({"erro": "Dados do pedido incompletos"}), 400

            valor_total = sum(float(item['preco_unitario_registrado']) * item['quantidade'] for item in itens)
            
            cur.execute("""
                INSERT INTO suagrafica_pedidos (cliente_id, valor_total, status_pedido)
                VALUES (%s, %s, %s) RETURNING id
            """, (cliente_id, valor_total, 'Aguardando Aprovação'))
            pedido_id = cur.fetchone()['id']
            
            item_values = [(pedido_id, item['produto_id'], item['quantidade'], item['preco_unitario_registrado']) for item in itens]
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO suagrafica_pedido_itens (pedido_id, produto_id, quantidade, preco_unitario_registrado)
                VALUES %s
                """,
                item_values,
                template="(%s, %s, %s, %s)",
                page_size=100
            )
            
            conn.commit()
            return jsonify({"mensagem": "Pedido criado com sucesso!", "pedido_id": pedido_id, "valor_total": float(valor_total)}), 201

    except Exception as e:
        traceback.print_exc()
        if conn: conn.rollback()
        return jsonify({"erro": str(e)}), 500
    finally:
        if conn: conn.close()

# ======================================================================
# 5. MÓDULO CHATBOT (ELO BOT - VENDAS & SUPORTE)
# Adicionado na versão 1.6
# ======================================================================

# --- FERRAMENTAS DO BANCO DE DADOS PARA O BOT ---
def tool_consultar_produtos(termo_busca):
    """Busca produtos no banco para oferecer ao cliente."""
    conn = get_db_connection()
    if not conn: return "Erro de conexão com banco de dados."
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT nome_produto, preco_minimo, multiplos_de, descricao 
            FROM suagrafica_produtos 
            WHERE esta_ativo = TRUE AND (nome_produto ILIKE %s OR descricao ILIKE %s)
            LIMIT 5
        """, (f'%{termo_busca}%', f'%{termo_busca}%'))
        produtos = cur.fetchall()
        if not produtos:
            return "Não encontrei produtos exatos com esse nome no catálogo."
        # Converte Decimal para float para o JSON
        for p in produtos:
            p['preco_minimo'] = float(p['preco_minimo'])
        return json.dumps(produtos, ensure_ascii=False)
    finally:
        conn.close()

def tool_consultar_pedido(pedido_id, cliente_id_verificacao=None):
    """Consulta status e detalhes de um pedido específico."""
    conn = get_db_connection()
    if not conn: return "Erro de conexão."
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        query = """
            SELECT id, valor_total, status_pedido, link_pagamento 
            FROM suagrafica_pedidos 
            WHERE id = %s
        """
        params = [pedido_id]
        
        # Adiciona verificação de segurança se o ID do cliente for passado
        if cliente_id_verificacao:
            query += " AND cliente_id = %s"
            params.append(cliente_id_verificacao)
            
        cur.execute(query, tuple(params))
        pedido = cur.fetchone()
        
        if not pedido:
            return "Pedido não encontrado ou não pertence a este cliente."
            
        pedido['valor_total'] = float(pedido['valor_total'])
        return json.dumps(pedido, ensure_ascii=False)
    finally:
        conn.close()

def tool_gerar_link_pagamento(pedido_id):
    """
    Gera um link e SALVA no banco (simulado).
    """
    conn = get_db_connection()
    if not conn: return "Erro de conexão."
    try:
        cur = conn.cursor()
        
        # Link Simulado (Substitua por lógica do Mercado Pago se tiver no futuro)
        link_template = f"https://www.elobrindes.com.br/checkout/pagamento?order={pedido_id}"
        
        cur.execute("""
            UPDATE suagrafica_pedidos 
            SET link_pagamento = %s, status_pedido = 'Aguardando Pagamento'
            WHERE id = %s
            RETURNING id
        """, (link_template, pedido_id))
        conn.commit()
        
        if cur.fetchone():
            return f"Link gerado com sucesso: {link_template}"
        else:
            return "Erro ao atualizar pedido. Verifique o ID."
    except Exception as e:
        return f"Erro ao gerar link: {str(e)}"
    finally:
        conn.close()

# --- CONTEXTO E PROMPT DO AGENTE ---
ELO_BRINDES_KNOWLEDGE = """
SOBRE A ELO BRINDES (Contexto Institucional):
- Site: www.elobrindes.com.br
- Quem somos: Líderes em brindes corporativos personalizados e materiais gráficos.
- Missão: Fortalecer marcas através de produtos de alta qualidade.
- Diferenciais: Agilidade na entrega, personalização premium, atendimento consultivo.
- Catálogo: Canetas, Agendas, Cadernos, Tecnologia (Powerbanks), Têxtil (Camisetas), Gráfica Rápida.
"""

SYSTEM_PROMPT = f"""
Você é o **Agente Comercial Virtual da Elo Brindes**, um especialista em vendas B2B e marketing.
Sua personalidade é: Entusiasta, Profissional, Persuasiva e Resolutiva.

{ELO_BRINDES_KNOWLEDGE}

SEUS OBJETIVOS:
1. **Vender (Hunter):** Não apenas responda. Ofereça produtos.
   - Se o cliente pede "Caneta", pergunte "É para algum evento específico? Temos modelos premium de metal ou promocionais de plástico."
   - **Técnica de Upsell:** Sempre sugira quantidades maiores para reduzir o custo unitário.
   - **Técnica de Cross-sell:** Se ele comprar Canetas, sugira Cadernos ou Blocos.
   - **Prevenção de Abandono:** Se o cliente parecer em dúvida sobre preço, enfatize a qualidade e o ROI (Retorno sobre Investimento) de brindes para a marca dele.

2. **Dar Suporte (Farmer):** Resolver dúvidas de pedidos existentes rapidamente para gerar confiança.
   - Se o cliente quiser pagar, USE A FERRAMENTA `gerar_pagamento` imediatamente. Não mande ele esperar.

3. **Operacional:**
   - Você TEM acesso ao banco de dados através de ferramentas JSON.
   - Se precisar de dados, solicite a ação correta no JSON de saída.

FORMATO DE RESPOSTA (OBRIGATÓRIO):
Você DEVE responder SEMPRE em JSON estrito com dois campos:
1. "botResponse": Sua fala com o cliente (use emojis, seja cordial).
2. "actionRequired": Um objeto descrevendo se você precisa consultar o banco.
   - Se nada for necessário: {{ "type": "none" }}
   - Para buscar produtos: {{ "type": "search_product", "term": "termo da busca" }}
   - Para ver status pedido: {{ "type": "check_order", "order_id": 123 }}
   - Para gerar link pagamento: {{ "type": "generate_payment", "order_id": 123 }}

EXEMPLO DE INTERAÇÃO DE VENDAS:
Cliente: "Quero canetas."
Bot (Pensamento): Buscar canetas no banco.
JSON Saída:
{{
  "botResponse": "Excelente escolha! As canetas são brindes de alto impacto. Vou buscar nossos modelos mais vendidos para você...",
  "actionRequired": {{ "type": "search_product", "term": "caneta" }}
}}

EXEMPLO DE FECHAMENTO:
Cliente: "Pode gerar o link do pedido 50?"
JSON Saída:
{{
  "botResponse": "Claro! Estou gerando seu link seguro da Elo Brindes agora mesmo...",
  "actionRequired": {{ "type": "generate_payment", "order_id": 50 }}
}}
"""

# --- ROTA DO CHAT ---
@app.route('/api/chat_vendas', methods=['POST'])
def chat_endpoint():
    # Verifica API KEY para não quebrar se não tiver configurado
    if not GEMINI_API_KEY:
        return jsonify({"response": "O Chatbot está temporariamente indisponível (Falta API KEY).", "action_taken": "error"}), 503

    data = request.json or {}
    history = data.get('history', []) 
    user_msg = data.get('message', '')
    client_id = data.get('client_id') 
    
    model = genai.GenerativeModel(
        'gemini-2.5-flash-preview-09-2025',
        system_instruction=SYSTEM_PROMPT
    )
    
    gemini_history = []
    for h in history:
        role = 'user' if h['role'] == 'user' else 'model'
        gemini_history.append({'role': role, 'parts': [h['content']]})
    
    gemini_history.append({'role': 'user', 'parts': [user_msg]})

    try:
        # 1. Primeira Chamada (Decisão)
        response = model.generate_content(
            gemini_history,
            generation_config=genai.types.GenerationConfig(
                temperature=0.7,
                response_mime_type="application/json"
            )
        )
        
        resp_text = response.text.replace('```json', '').replace('```', '').strip()
        ai_data = json.loads(resp_text)
        
        action = ai_data.get('actionRequired', {'type': 'none'})
        bot_text = ai_data.get('botResponse', '')
        
        # 2. Execução de Ferramentas
        tool_result = None
        
        if action['type'] == 'search_product':
            print(f"🔍 [Bot] Buscando produtos: {action['term']}")
            tool_result = tool_consultar_produtos(action['term'])
            
        elif action['type'] == 'check_order':
            print(f"🔍 [Bot] Verificando pedido: {action['order_id']}")
            tool_result = tool_consultar_pedido(action['order_id'], client_id)
            
        elif action['type'] == 'generate_payment':
            print(f"💰 [Bot] Gerando pagamento pedido: {action['order_id']}")
            tool_result = tool_gerar_link_pagamento(action['order_id'])

        # 3. Segunda Chamada (Se houve ferramenta)
        if tool_result:
            prompt_com_dados = f"""
            DADOS OBTIDOS DO SISTEMA:
            {tool_result}
            
            Com base nesses dados acima, dê a resposta final ao cliente. 
            Se for produto, apresente de forma atraente com preço.
            Se for link, envie o link.
            """
            
            final_response = model.generate_content(
                [{'role': 'user', 'parts': [prompt_com_dados]}],
                generation_config=genai.types.GenerationConfig(
                    temperature=0.7,
                    response_mime_type="application/json"
                )
            )
            
            final_resp_text = final_response.text.replace('```json', '').replace('```', '').strip()
            final_json = json.loads(final_resp_text)
            bot_text = final_json.get('botResponse', 'Aqui estão os dados.')

        return jsonify({
            "response": bot_text,
            "action_taken": action['type']
        })

    except Exception as e:
        print(f"🔴 Erro Chatbot: {e}")
        return jsonify({"response": "Desculpe, tive um lapso de memória momentâneo. Pode repetir?", "error": str(e)}), 500


if __name__ == '__main__':
    setup_database()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)