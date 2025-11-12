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

# ======================================================================
# API BACKEND - [SUA GRÁFICA] B2B PORTAL
# Versão: 1.0 (Fase 1: Admin CRUD)
# ======================================================================

load_dotenv()
app = Flask(__name__)
# Permitir que o admin HTML converse com este backend
CORS(app, resources={r"/api/*": {"origins": "*"}}) 

# --- CONFIGURAÇÃO: BANCO DE DADOS ---
# Puxa a URL do seu arquivo .env ou das variáveis de ambiente do Render
DATABASE_URL = os.environ.get("DATABASE_URL")

# --- SIMULAÇÃO DE SESSÃO (Para MVP) ---
# Armazena tokens de admin ativos: { "token_uuid": admin_id }
ADMIN_SESSIONS = {}

def get_db_connection():
    """Abre uma conexão com o PostgreSQL."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"🔴 ERRO AO CONECTAR NO DB: {e}")
        return None

# ======================================================================
# 1. SETUP DO BANCO DE DADOS (Baseado no seu script de 5 tabelas)
# ======================================================================
def setup_database():
    """Verifica e cria as 5 tabelas B2B se não existirem."""
    conn = get_db_connection()
    if not conn: return
    
    # Lista dos 5 comandos SQL
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
            status_pedido VARCHAR(50) DEFAULT 'Aguardando Pagamento',
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
        print("ℹ️  [DB] Verificando arquitetura B2B de 5 tabelas...")
        for i, cmd in enumerate(SQL_COMMANDS):
            cur.execute(cmd)
            print(f"  [DB {i+1}/5] Tabela OK.")
        
        # --- SEED INICIAL (Admin Padrão) ---
        cur.execute("SELECT COUNT(*) FROM suagrafica_admin WHERE username = 'leanttro'")
        if cur.fetchone()[0] == 0:
             # Usuário: leanttro | Senha: 12345 (CORRIGIDO para as credenciais do usuário)
             cur.execute("INSERT INTO suagrafica_admin (username, chave_admin) VALUES (%s, %s)", ('leanttro', '12345'))
             print("✅ [DB] Admin padrão (leanttro/12345) criado.")
        
        # Garante que o ID do admin seja usado para os dados de teste
        cur.execute("SELECT id FROM suagrafica_admin WHERE username = 'leanttro' LIMIT 1")
        admin_id_fetch = cur.fetchone()
        if admin_id_fetch:
            admin_id = admin_id_fetch[0]

            # Seed de 1 Cliente e 1 Produto para teste (Verifica se já existem)
            cur.execute("SELECT COUNT(*) FROM suagrafica_clientes WHERE codigo_acesso = 'CLIENTE123'")
            if cur.fetchone()[0] == 0:
                 cur.execute("INSERT INTO suagrafica_clientes (admin_id, nome_cliente, cnpj, codigo_acesso) VALUES (%s, %s, %s, %s)",
                             (admin_id, 'Cliente Teste S.A', '00.000.000/0001-00', 'CLIENTE123'))
            
            cur.execute("SELECT COUNT(*) FROM suagrafica_produtos WHERE codigo_produto = 'ER1458-AZU'")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT INTO suagrafica_produtos (codigo_produto, nome_produto, preco_minimo, multiplos_de) VALUES (%s, %s, %s, %s)",
                            ('ER1458-AZU', 'CANETA METAL AZUL', 2.10, 50))
                print("✅ [DB] Dados de teste (Cliente e Produto) verificados/criados.")

        conn.commit()
        print("✅ [DB] Arquitetura B2B pronta.")

    except Exception as e:
        print(f"🔴 ERRO NO SETUP DO DB: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

# ======================================================================
# 2. MIDDLEWARE & AUTENTICAÇÃO
# ======================================================================
def check_auth(request):
    """Verifica se o request tem um token de admin válido."""
    token = request.headers.get('Authorization')
    if not token: return None
    token = token.replace('Bearer ', '')
    return ADMIN_SESSIONS.get(token) # Retorna admin_id ou None

@app.route('/api/admin/login', methods=['POST'])
def login_admin():
    data = request.json or {}
    username = data.get('username')
    chave_admin = data.get('chave_admin') # Senha em texto puro

    if not username or not chave_admin:
        return jsonify({"erro": "Credenciais incompletas"}), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        # Lógica de login simples (comparando texto puro)
        cur.execute("SELECT id FROM suagrafica_admin WHERE username = %s AND chave_admin = %s", (username, chave_admin))
        admin = cur.fetchone()
        
        if admin:
            token = str(uuid.uuid4())
            ADMIN_SESSIONS[token] = admin[0] # Salva admin_id na sessão
            return jsonify({"mensagem": "Login admin realizado", "token": token, "admin_id": admin[0]})
        else:
            return jsonify({"erro": "Usuário ou chave inválidos"}), 401
    finally:
        if conn: conn.close()
        
# ======================================================================
# 3. ENDPOINTS - ADMIN: DASHBOARD
# ======================================================================
@app.route('/api/admin/dashboard_stats', methods=['GET'])
def admin_stats():
    if not check_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM suagrafica_clientes WHERE status_acesso = 'Ativo'")
        clientes = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM suagrafica_produtos WHERE esta_ativo = TRUE")
        produtos = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM suagrafica_pedidos WHERE status_pedido = 'Aguardando Pagamento'")
        pedidos_pendentes = cur.fetchone()[0]
        
        return jsonify({
            "stat_clientes": clientes,
            "stat_produtos": produtos,
            "stat_pedidos": pedidos_pendentes
        })
    finally:
        if conn: conn.close()

# ======================================================================
# 4. ENDPOINTS - ADMIN: CRUD PRODUTOS
# ======================================================================

@app.route('/api/admin/produtos', methods=['GET', 'POST'])
def admin_gerenciar_produtos():
    admin_id = check_auth(request)
    if not admin_id: return jsonify({"erro": "Não autorizado"}), 403
    
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # GET: Lista todos os produtos
        if request.method == 'GET':
            cur.execute("SELECT * FROM suagrafica_produtos ORDER BY nome_produto")
            produtos = cur.fetchall()
            for p in produtos:
                 p['preco_minimo'] = float(p['preco_minimo']) # Converte Decimal para float
            return jsonify(produtos)
            
        # POST: Adiciona um novo produto
        elif request.method == 'POST':
            data = request.json or {}
            
            # Validação (Campos do XBZ)
            if not data.get('codigo_produto') or not data.get('nome_produto') or not data.get('preco_minimo'):
                return jsonify({"erro": "Código, Nome e Preço Mínimo são obrigatórios."}), 400

            cur.execute("""
                INSERT INTO suagrafica_produtos (codigo_produto, nome_produto, preco_minimo, multiplos_de, descricao, imagem_url, esta_ativo, estoque_disponivel)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (
                data.get('codigo_produto'), data.get('nome_produto'), data.get('preco_minimo'),
                data.get('multiplos_de', 1), data.get('descricao'), data.get('imagem_url'),
                data.get('esta_ativo', True), data.get('estoque_disponivel', True)
            ))
            conn.commit()
            return jsonify({"mensagem": "Produto adicionado com sucesso!", "id": cur.fetchone()['id']}), 201

    except Exception as e:
        if conn: conn.rollback()
        print(traceback.format_exc())
        # Trata erro de código duplicado
        if "unique constraint" in str(e).lower():
            return jsonify({"erro": "Este Código de Produto já existe."}), 409
        return jsonify({"erro": f"Erro interno: {e}"}), 500
    finally:
        if conn: conn.close()

@app.route('/api/admin/produtos/<int:id>', methods=['GET', 'PUT', 'DELETE'])
def admin_crud_produto_by_id(id):
    """Busca, atualiza ou deleta um produto."""
    admin_id = check_auth(request)
    if not admin_id: return jsonify({"erro": "Não autorizado"}), 403
    
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # GET: Busca um produto para o modal de edição
        if request.method == 'GET':
            cur.execute("SELECT * FROM suagrafica_produtos WHERE id = %s", (id,))
            produto = cur.fetchone()
            if not produto: return jsonify({"erro": "Produto não encontrado"}), 404
            produto['preco_minimo'] = float(produto['preco_minimo'])
            return jsonify(produto)
            
        # PUT: Atualiza o produto
        elif request.method == 'PUT':
            data = request.json or {}
            cur.execute("""
                UPDATE suagrafica_produtos 
                SET codigo_produto = %s, nome_produto = %s, preco_minimo = %s, multiplos_de = %s, 
                    descricao = %s, imagem_url = %s, esta_ativo = %s, estoque_disponivel = %s
                WHERE id = %s
            """, (
                data.get('codigo_produto'), data.get('nome_produto'), data.get('preco_minimo'),
                data.get('multiplos_de'), data.get('descricao'), data.get('imagem_url'),
                data.get('esta_ativo'), data.get('estoque_disponivel'), id
            ))
            conn.commit()
            return jsonify({"mensagem": "Produto atualizado com sucesso!"})

        # DELETE: Deleta o produto
        elif request.method == 'DELETE':
            cur.execute("DELETE FROM suagrafica_produtos WHERE id = %s", (id,))
            conn.commit()
            return jsonify({"mensagem": "Produto deletado com sucesso!"})

    except Exception as e:
        if conn: conn.rollback()
        if "unique constraint" in str(e).lower():
            return jsonify({"erro": "Este Código de Produto já existe."}), 409
        return jsonify({"erro": f"Erro interno: {e}"}), 500
    finally:
        if conn: conn.close()

# ======================================================================
# 5. ENDPOINTS - ADMIN: CRUD CLIENTES
# ======================================================================

@app.route('/api/admin/clientes', methods=['GET', 'POST'])
def admin_gerenciar_clientes():
    admin_id = check_auth(request)
    if not admin_id: return jsonify({"erro": "Não autorizado"}), 403
    
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # GET: Lista todos os clientes
        if request.method == 'GET':
            cur.execute("SELECT * FROM suagrafica_clientes WHERE admin_id = %s ORDER BY nome_cliente", (admin_id,))
            return jsonify(cur.fetchall())
            
        # POST: Adiciona um novo cliente
        elif request.method == 'POST':
            data = request.json or {}
            
            if not data.get('nome_cliente') or not data.get('codigo_acesso'):
                return jsonify({"erro": "Nome do Cliente e Código de Acesso são obrigatórios."}), 400
            
            cur.execute("""
                INSERT INTO suagrafica_clientes (admin_id, nome_cliente, cnpj, email_contato, codigo_acesso, status_acesso)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (
                admin_id, data.get('nome_cliente'), data.get('cnpj'), 
                data.get('email_contato'), data.get('codigo_acesso'), data.get('status_acesso', 'Ativo')
            ))
            conn.commit()
            return jsonify({"mensagem": "Cliente adicionado com sucesso!", "id": cur.fetchone()['id']}), 201

    except Exception as e:
        if conn: conn.rollback()
        if "unique constraint" in str(e).lower():
            return jsonify({"erro": "Este Código de Acesso ou CNPJ já está em uso."}), 409
        return jsonify({"erro": f"Erro interno: {e}"}), 500
    finally:
        if conn: conn.close()

@app.route('/api/admin/clientes/<int:id>', methods=['DELETE'])
def admin_delete_cliente(id):
    """Deleta um cliente."""
    admin_id = check_auth(request)
    if not admin_id: return jsonify({"erro": "Não autorizado"}), 403
    
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM suagrafica_clientes WHERE id = %s AND admin_id = %s", (id, admin_id))
        conn.commit()
        if cur.rowcount == 0:
            return jsonify({"erro": "Cliente não encontrado ou não pertence a este admin"}), 404
        return jsonify({"mensagem": "Cliente deletado com sucesso!"})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"erro": f"Erro interno: {e}"}), 500
    finally:
        if conn: conn.close()


# ======================================================================
# INICIALIZAÇÃO
# ======================================================================
if __name__ == '__main__':
    # Garante que as tabelas existam ao iniciar localmente
    setup_database()
    port = int(os.environ.get("PORT", 5000))
    # Para o Render/Cloud, você deve usar gunicorn, não app.run() em produção
    app.run(host='0.0.0.0', port=port, debug=True)