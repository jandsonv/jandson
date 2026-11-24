# Robô de passagens em promoção (Aracaju e Salvador)

Procura passagens baratas **em dinheiro** saindo de AJU e SSA e, quando acha uma, consulta **as milhas naquela mesma rota e data** nos sites da Smiles, LATAM Pass e Azul Fidelidade. O resultado chega no Telegram com as duas opções lado a lado.

## Como funciona

1. **Radar em dinheiro** — consulta a API do Travelpayouts (Aviasales) e separa o que está abaixo do teto definido no `config.yaml`, ou bem abaixo do que a rota costuma custar.
2. **Milhas na data certa** — para cada promoção, abre o Chrome do seu computador na busca de cada programa, já com rota e data, e lê o menor valor em milhas. São poucas consultas por rodada, com pausa entre elas.
3. **Alerta no Telegram** — preço em dinheiro, voo, milhas por programa, equivalente em reais pelo valor do milheiro e os links de cada busca.
4. **Sites de promoção** — avisa também quando o Melhores Destinos ou o Passageiro de Primeira publicam algo que cita Aracaju ou Salvador, ou bônus de transferência para os seus programas.

Se um site de milhas pedir verificação ou mudar de layout, o robô não tenta burlar: o alerta sai do mesmo jeito, com o link da busca pronto para você abrir.

## Instalação (Windows)

1. Instale o [Python](https://www.python.org/downloads/) marcando **Add Python to PATH**, e tenha o Google Chrome instalado.
2. Baixe este repositório e dê dois cliques em `instalar.bat`.
3. Preencha o arquivo `.env`:
   - `TRAVELPAYOUTS_TOKEN`: crie uma conta gratuita em travelpayouts.com e copie o *API token* do perfil.
   - `TELEGRAM_TOKEN`: no Telegram, fale com **@BotFather**, envie `/newbot` e copie o token.
   - `TELEGRAM_CHAT_ID`: mande "oi" para o seu bot e rode `.venv\Scripts\python -m robo telegram-id`.
4. Teste: `.venv\Scripts\python -m robo testar-telegram`
5. Primeira rodada vendo o resultado na tela, sem enviar nada: `.venv\Scripts\python -m robo rodar --simular`
6. Para rodar sozinho a cada 3 horas, dê dois cliques em `agendar.bat`. O registro fica em `dados\log.txt`.

## Comandos

| Comando | O que faz |
|---|---|
| `python -m robo rodar` | Rodada completa e envio dos alertas |
| `python -m robo rodar --simular` | Mostra as mensagens na tela, sem enviar |
| `python -m robo rodar --sem-milhas` | Não abre o navegador; manda só os links de milhas |
| `python -m robo rodar --fotos` | Envia a captura de tela quando não conseguir ler as milhas |
| `python -m robo milhas AJU GRU 2027-02-10` | Consulta milhas numa rota e data (para testar a leitura) |

## Ajustes

Tudo fica no `config.yaml`: aeroportos, tetos de preço, janela de datas, valor do milheiro, número de consultas por rodada e os modelos de link de cada programa.

## Limitações

- Os preços em dinheiro vêm de buscas recentes de outras pessoas, então podem estar defasados e a cobertura de rotas menos procuradas é irregular. Confira sempre no link.
- A leitura de milhas pega o menor valor que aparece na página de resultados e não inclui as taxas. Em viagens de ida e volta, a consulta de milhas é feita só para o trecho de ida.
- O robô só consulta milhas com o computador ligado e abre uma janela do Chrome enquanto trabalha.
- A consulta usa um perfil de navegador separado (`perfil-navegador/`), sem login nas suas contas.

## Testes

```
pip install pytest
python -m pytest tests
```
