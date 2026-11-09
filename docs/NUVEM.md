# Colocar o sistema na internet (vários setores)

Um servidor Linux pequeno (VPS de 1 GB, ~R$ 30–50/mês) aguenta a Prefeitura inteira:
o banco continua sendo o SQLite (um arquivo), cada batida é uma linha minúscula.
Postgres só vale a pena se um dia houver vários servidores — não é o caso.

## 1. Servidor (Ubuntu 24.04)

```bash
sudo apt update && sudo apt install -y python3-venv git caddy
sudo useradd -m -s /bin/bash ponto
sudo -iu ponto
git clone https://github.com/anndreluizz/sistema-de-ponto.git app && cd app
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
exit
```

## 2. Serviço (liga sozinho e reinicia se cair)

`/etc/systemd/system/ponto.service`:

```ini
[Unit]
Description=Sistema de ponto
After=network.target

[Service]
User=ponto
WorkingDirectory=/home/ponto/app
Environment=PONTO_NUVEM=1
ExecStart=/home/ponto/app/.venv/bin/waitress-serve --listen=127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now ponto
```

## 3. HTTPS (obrigatório: sem ele o tablet não libera a câmera)

Aponte o domínio (ex.: `ponto.prefeitura.am.gov.br`) para o IP do servidor e em `/etc/caddy/Caddyfile`:

```
ponto.prefeitura.am.gov.br {
    reverse_proxy 127.0.0.1:5000
}
```

```bash
sudo systemctl reload caddy
```

O Caddy tira e renova o certificado sozinho.

## 4. Primeiro acesso

1. Entre com `admin` / `admin123` e troque a senha.
2. **Cadastros › Setores**: um por secretaria/escola.
3. **Sistema › Usuários**: o RH (perfil RH) e um usuário por chefe de setor (perfil Direção + setor dele).
   O prefeito: perfil Direção, setor "Todos os setores" — vê o painel geral sem poder alterar nada.
4. Em cada tablet, entre como admin e autorize o totem em Configurações.

## 5. Backup

O backup automático já roda. Na nuvem, configure em **Configurações › Backup** uma pasta
sincronizada para fora do servidor (ex.: rclone para o Google Drive da Prefeitura) — o aviso
amarelo do painel some quando há cópia fora do disco.

## Totem sem internet

Se a internet do setor cair, o tablet guarda as batidas e envia sozinho quando voltar
(aviso amarelo no canto da tela mostra quantas estão guardadas). Limite: 4 dias.
Não recarregue a página do totem enquanto estiver sem internet — ela precisa da rede para abrir.

## Totem abrindo sem internet

Com HTTPS (nuvem), o tablet guarda a tela do totem (`/sw.js`): se reiniciar sem internet, a página abre do mesmo jeito e as batidas vão para a fila. Pelo IP da rede da escola (HTTP) o navegador não permite — lá continua valendo só a fila de batidas.

## Meu Ponto (servidor)

O RH gera o PIN na ficha do funcionário (precisa ter matrícula). O servidor entra em `/meu-ponto/entrar`, vê o mês e pede justificativa com foto do atestado. O chefe do setor (perfil Direção preso ao setor) ou o RH respondem em **Pedidos**; aprovado vira justificativa com o anexo.
