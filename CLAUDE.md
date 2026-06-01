# Nodeflow Faturix — Estado do Projeto

## Contexto
Projeto de treino de agente local Ollama para leitura e extração estruturada de documentos financeiros da empresa Nodeflow.

## Estado atual
- **Phase 1** ✅ — Propósito e persona definidos
- **Phase 2** ✅ — SYSTEM prompt completo e aprovado
- **Phase 3** ✅ — 21 exemplos de conversas construídos e aprovados
- **Phase 4** ✅ — Modelfile exportado, agente criado e testado
- **Phase 5** 🔄 — Em curso (avaliação e iteração contínua)

## Agente
- **Nome:** Nodeflow Faturix
- **Modelo base:** mistral:7b (migrado de llama3.1:8b)
- **Ficheiro:** `C:/projetos/Agente/Modelfile`
- **Comando para correr:** `ollama run nodeflow-faturix`
- **Comando para recriar:** `ollama create nodeflow-faturix -f Modelfile`

## App Web (Streamlit)
- **Ficheiro:** `C:/projetos/Agente/app.py`
- **Comando para correr:** `python -m streamlit run C:\projetos\Agente\app.py`
- **Atalho desktop:** `Nodeflow Faturix.lnk` no Ambiente de Trabalho
- **URL local:** `http://localhost:8501`
- **Funcionalidades:**
  - Tab "Carregar PDF" — upload de PDF digital ou imagem (JPG/PNG)
  - Tab "Gmail" — ligação via IMAP + App Password, lista emails com PDFs
  - Tab "Outlook" — ligação via IMAP, lista emails com PDFs
  - Texto extraído e JSON lado a lado

## Pipeline de processamento — PDF
```
PDF
  → pdfplumber (extração com deteção de tabelas)
  → _text_quality() — se qualidade < 0.75 ou linhas começam com "="
      → _pdf_to_images_ocr() — Tesseract a 400 DPI + _clean_ocr()
  → _scan_pdf_for_qr() — renderiza páginas a 200 DPI, deteta QR Code AT
      → parse_at_qrcode() — campos financeiros exatos (NIF, IBAN, totais)
  → normalize_doc_type() — normaliza tipo antes de enviar ao modelo
  → call_faturix() — com retry automático se rejeição indevida
  → Faturix → JSON final
```

## Pipeline de processamento — Imagem (JPG/PNG)
```
Foto/imagem
  → OpenCV → QR Code AT lido e parseado em Python (campos estruturados)
  → Tesseract OCR + _clean_ocr() → texto do documento
  → normalize_doc_type() + call_faturix()
  → Faturix → JSON final
```

## Funções de pré-processamento (app.py)
- **`_text_quality(text)`** — retorna 0.0 se linhas começam com "=" (ruído de layout complexo); caso contrário calcula rácio de ruído
- **`_clean_ocr(text)`** — repara artefacto de encoding `¦` → `ñ` (PDFs antigos Windows-1252); strip de "=" e "|" no início das linhas; remove duplicados decimais colapsados (ex: "109,15€ 10915" → "109,15€")
- **`_pdf_to_images_ocr(pdf_bytes)`** — fallback OCR: renderiza PDF a 400 DPI com PyMuPDF + Tesseract (por+eng) + _clean_ocr
- **`_scan_pdf_for_qr(pdf_bytes)`** — renderiza PDF a 200 DPI, corre read_qr_codes() em cada página; se encontrar QR AT (*:) devolve parse_at_qrcode()
- **`normalize_doc_type(text)`** — substitui "Fatura Simplificada" → "Fatura", "Fatura-Recibo" → "Fatura", remove labels "original/duplicado"; sanitiza símbolo de polegadas `(\d)"` → `$1''` para evitar que o modelo quebre a geração de JSON; repara artefacto de encoding `¦` → `ñ` (cobre caminho pdfplumber onde `_clean_ocr` não é chamada)
- **`_build_supplier_hint(text)`** — se o documento tem "Invoice nbr." mas não tem "Supplier:" (label com dois pontos), adiciona hint ao modelo para usar fornecedor_nome: null quando não identificável
- **`_sanitize_result(text, result)`** — pós-processamento determinístico: (0a) extrai fornecedor de "X is registered for VAT no. Y"; (0b) extrai data_vencimento de "VENCIMIENTOS: DD/MM/YYYY"; (0c) extrai fornecedor_nome de "Agency\nNOME" quando modelo retorna null; (1) remove NIFs de nib_contas_bancarias (PT/ES/DE + <15 chars); (2) corrige imposto_taxa: 0 quando texto tem "VAT 0%"/"V.A.T. 0 %"/"Exempt from VAT" e imposto_valor é 0 ou null; (3) fornecedor_pais: null quando fornecedor_nif é null — sem NIF não é possível confirmar o país; (4) numero_fatura de "F A C T U R A : NNN" (letras espaçadas, formato espanhol); (4b) numero_fatura de "Invoice nbr." — tem precedência sobre o que o modelo extraiu; (5) valor_liquido = valor_total quando imposto_taxa=0 e imposto_valor=0; (6) unidade preenchida quando o modelo retorna null em todas as linhas mas o documento tem unidade consistente (ex: "UN"); (7) fornecedor_nif corrigido quando CIF espanhol sem prefixo ES (via "VAT - ESA..." no texto); (8) referencia limpa de prefixo numérico de linha quando coluna "Línea" existe; (10) total=0 em linhas com desconto 100% (Caso A: modelo extraiu desconto "100"; Caso B: texto mostra "REF...100 0,00"); (11) loja_armazem extraído de "ARMAZEM N" ou "NORAUTO XXX (MAG NNN)" quando modelo retorna null; (12) referencia numérica de 5-8 dígitos corrigida para código do artigo na linha seguinte — Caso A: código alfanumérico (MUCHL0009); Caso B: código numérico seguido de descrição (9901009); (13) quantidade corrigida quando preco_unitario × quantidade ≠ total e sem desconto; (14) prefixo de referência removido da descricao quando modelo inclui o código de artigo no início da descrição; (15) remove linhas fantasma nulas (referencia preenchida mas descricao/quantidade/total todos null) — também remove refs all-uppercase (ALBARAN, PEDIDO) mesmo com total não-null; (16) deduplica linhas com referencia repetida — mantém a com descricao preenchida ou maior total; (17) remove referencias numéricas orfãs (5-8 dígitos, sem descricao, isoladas no texto sem texto na mesma linha); (18) extrai descricao do texto quando modelo retornou null — Padrão A: "REF DESC QTY UN PRICE DTO TOTAL" (Ascendeo/ES), Padrão B: "REF DESC QTY PRICE UN" (fallback); (19) corrige qty/preco/total quando modelo extraiu valores errados — compara total do modelo com total extraído do texto via padrão "REF ... QTY UN PRICE DTO TOTAL"; (20) recupera/completa linhas no formato Ascendeo (header "ARTICULO...PRECIO...DTO") — adiciona linhas em falta e preenche campos críticos nulos (tipo, quantidade, preco_unitario, total, unidade) a partir do texto; (21) corrige valor_total de "TOTAL . . . : NNN,NN" (formato espanhol) quando modelo confunde com outro número (ex: número de fatura interpretado como total); também corrige valor_liquido se imposto_taxa=0
- **`call_faturix(text)`** — envia ao modelo com hint contextual; retry automático se rejeição indevida; aplica _sanitize_result no output

## Retry logic (call_faturix)
Se o modelo devolver `{"erro": "Documento rejeitado", "motivo": "Tipo de documento não suportado: Fatura"}` (ou Invoice/Nota de Crédito/Encomenda), a app deteta a contradição e reenvia o prompt com:
```
ATENÇÃO: O tipo de documento é 'fatura' — processa-o e extrai os dados em JSON.
```

## OCR — Tesseract
- **Instalado em:** `C:\Program Files\tesseract.exe`
- **Línguas:** `por+eng`
- **DPI para PDF:** 400 (melhor qualidade para documentos complexos)
- **DPI para QR scan:** 200 (suficiente para deteção de QR codes)

## Script de linha de comandos
- **Ficheiro:** `C:/projetos/Agente/faturix.py`
- **Uso:** `python faturix.py Faturas/fatura.pdf`
- Só para PDFs digitais (sem OCR)

## Dependências Python
```
pip install -r C:\projetos\Agente\requirements.txt
```
```
streamlit, pymupdf, pdfplumber, requests, pytesseract, pillow, opencv-python
```

## Documentos suportados
- Faturas de fornecedores (nacionais e estrangeiras)
- Notas de crédito (nacionais e estrangeiras)
- Encomendas a fornecedores (nacionais e estrangeiras)
- Artigos (produtos físicos) e serviços

## Funcionalidades principais
- Extração de dados estruturados em JSON
- QR Code AT com precedência sobre texto (faturas nacionais) — tanto em imagens como em PDFs
- Alerta de divergência QR Code vs texto
- Comandos de idioma: /TP /TI /TF /TE
- Valores negativos em notas de crédito
- Campo `taxa: "%"` como indicador de percentagem
- Múltiplas taxas de IVA por linha
- Múltiplos armazéns por fatura (cabeçalho ou por linha)
- Identificação do tipo de documento antes de qualquer validação
- Rejeição por tipo de documento não suportado
- Retry automático quando modelo rejeita tipo suportado

## Regras absolutas (NUNCA)
- Inventar ou inferir valores
- Processar faturas sem NIF/VAT
- Processar faturas sem descrição de artigo/serviço
- Processar faturas sem valores monetários
- Processar notas de crédito sem fatura de origem
- Processar documentos com texto oculto
- Processar documentos que não sejam faturas, notas de crédito ou encomendas
- Calcular valores — extrai sempre do documento, nunca faz aritmética
- Adicionar texto fora do JSON

## Ordem de processamento
1. Identifica o tipo de documento
2. Se não suportado, rejeita imediatamente
3. Só depois valida campos obrigatórios (NIF, descrições, valores)

## Campos JSON — Fatura
```
tipo_documento, fornecedor_nome, fornecedor_pais, fornecedor_nif,
numero_fatura, numero_contrato_processo, data_emissao, data_vencimento,
moeda, taxa, valor_liquido, imposto_taxa, imposto_valor, valor_total,
retencao_taxa, retencao_valor, valor_a_pagar,
nib_contas_bancarias, loja_armazem, alerta_qrcode, alerta_qrcode_detalhe,
qrcode_ilegivel, linhas[]
```

## Campos JSON — Nota de Crédito
```
tipo_documento, fornecedor_nome, fornecedor_pais, fornecedor_nif,
numero_nota_credito, numero_fatura_origem, numero_contrato_processo,
data_emissao, motivo, moeda, taxa, valor_liquido, imposto_taxa,
imposto_valor, valor_total, nib_contas_bancarias, loja_armazem, linhas[]
```

## Campos JSON — Encomenda
```
tipo_documento, fornecedor_nome, fornecedor_pais, fornecedor_nif,
numero_encomenda, numero_contrato_processo, data_emissao, prazo_entrega,
moeda, taxa, valor_liquido, imposto_taxa, imposto_valor, valor_total,
condicoes_pagamento, nib_contas_bancarias, loja_armazem, linhas[]
```

## Campos JSON — Linhas
```
tipo ("artigo" ou "servico"), referencia, descricao, quantidade,
unidade, preco_unitario, desconto (% ou "25+5" composto, null se ausente),
imposto_taxa (por linha se taxas diferentes),
loja_armazem (por linha se armazéns diferentes), total
```

## Exemplos de treino (31)
1. Fatura nacional com artigos ✅
2. Fatura estrangeira com serviços ✅
3. Nota de crédito ✅
4. QR Code sem divergência ✅
5. QR Code com divergência ✅
6. Rejeição: sem NIF ✅
7. Rejeição: sem descrição ✅
8. Rejeição: texto oculto ✅
9. Encomenda com IVA ✅
10. Múltiplas taxas de IVA ✅
11. Múltiplas lojas — mesmo armazém no cabeçalho ✅
12. Múltiplas lojas — armazém por linha ✅
13. Rejeição: proposta comercial ✅
14. Rejeição: orçamento ✅
15. Rejeição: recibo ✅
16. Fatura USD (formato ColdTech — "Artigo:", "Total:") ✅
17. Encomenda estrangeira (FreezeTech UK — prazo_entrega, condicoes_pagamento, loja_armazem) ✅
18. Fatura USD (formato TechParts — "Items:", "Subtotal:", "Total Due:") ✅
19. Nota de crédito estrangeira (FreezeTech UK, valores negativos) ✅
20. Fatura Simplificada (Brico Depot — QR Code AT, FATURA SIMPLIFICADA normalizada) ✅
21. Fatura Simplificada Nº (formato português — tabela complexa, texto limpo) ✅
22. Fatura Simplificada Nº (OCR real garbled — AstorGes, tabela gráfica complexa) ✅
23. Fatura Simplificada Nº (OCR limpo — múltiplas taxas IVA 23%+6%, AstorGes) ✅
24. Fatura de serviço com retenção na fonte 25% (retencao_taxa, retencao_valor, valor_a_pagar) ✅
25. Fatura EU com VAT (0%) Intra-EU — imposto_taxa: 0 em vez de null ✅
26. Fatura estrangeira com desconto composto "25+5", 100% desconto (oferta), V.A.T. 0%, Invoice nbr. ✅
27. Fatura ES com V.A.T. 0% em formato key-value + desconto simples ✅
28. Invoice com buyer no topo sem label "Supplier:" → fornecedor_nome: null ✅
29. Fatura ES Garmin Iberia — CIF+VAT separados, I.V.A. 0%, "Factura NNNNN", coluna "Línea", unidade "Each" ✅
30. Fatura EU (TNT Exportacion → Norauto, "Invoice nbr.", loja_armazem "ARMAZEM 11", linha com desconto 100%, V.A.T. 0%) ✅
31. Fatura ES (Ascendeo Iberia → Norauto Barreiro, "F A C T U R A", Exempt from VAT, VENCIMIENTOS, códigos secundários entre linhas, loja "MAG 615") ✅

## Decisão tomada
Ficamos com **mistral:7b**. gemma4:e4b testado e rejeitado para extração de texto (inventava valores).

## Limitações conhecidas (mistral:7b)
- `imposto_taxa: 0` quando taxa 0% em formato key-value (`"VAT (0%)"`, `"V.A.T. 0 %: 0,00 EUR"`) ✅
- `imposto_taxa: null` quando `"V.A.T. 0 %"` aparece apenas como cabeçalho de coluna sem valor explícito na linha — limitação de layout; `imposto_valor: 0.00` é extraído correctamente
- `fornecedor_nome: null` quando fatura não tem label "Supplier:" e o comprador aparece no topo — layout ambíguo sem remédio por texto
- `valor_liquido`, `imposto_valor`, `valor_total` podem retornar `null` em encomendas (rodapé "Subtotal / VAT / Total"). Os valores das linhas são sempre corretos.
- `numero_contrato_processo` pode ser omitido do output mesmo sendo null.
- PDFs com tabelas gráficas complexas (múltiplas colunas sobrepostas): OCR produz texto parcialmente ilegível → linhas ficam com dados incompletos (referencia garbled), mas **todos os campos financeiros críticos são extraídos corretamente** (tipo_documento, numero_fatura, data_emissao, valor_liquido, imposto_valor, valor_total, e totais por linha).

## Fix aplicado — encoding ¦ → ñ em PDFs antigos (2026-05-24)
PDFs espanhóis de ~2010-2015 com encoding Windows-1252 produziam `¦` (U+00A6, broken bar) onde devia aparecer `ñ` — ex: `"Tama¦o"` em vez de `"Tamaño"`.
- **`_clean_ocr()`** — `text.replace('¦', 'ñ')` antes do processamento linha a linha (cobre caminho Tesseract)
- **`normalize_doc_type()`** — mesmo replace adicionado (cobre caminho pdfplumber, onde `_clean_ocr` não é chamada)
O carácter `¦` não aparece legitimamente em texto de faturas — substituição segura.

## Fix aplicado — decimais OCR (2026-05-12)
Tesseract produzia duplicados colapsados: `109,15€ 10915` na mesma linha de tabela complexa.
`_clean_ocr()` agora remove o duplicado via regex: quando `NNNNN` é a concatenação exata do inteiro+decimal de um valor `NNN,NN€` adjacente, o colapsado é eliminado.
Resultado no caso AstorGes: `linhas[0].total: 10915` → `109.15` ✅

## Fixes aplicados — fatura espanhola formato FACTURA (2026-05-14)
Caso de teste: Integral Office Comunicaciones SLU → Norauto Portugal (15 artigos, Exempt from VAT).
- **Fix 4 — numero_fatura:** modelo retornava null para "F A C T U R A : 894.352 F" (letras espaçadas). `_FACTURA_SPACED` regex extrai deterministicamente quando modelo falha. Também `_FACTURA_NUMBER` para formato normal "Factura NNNNN".
- **Fix 5 — valor_liquido:** modelo retornava null quando não há subtotal explícito e IVA=0%. Quando `imposto_taxa==0` e `imposto_valor==0`, `valor_liquido` é copiado de `valor_total`.
- **Fix 6 — unidade:** modelo retornava null em todas as linhas apesar de "UN" visível no texto. Quando todas as linhas têm `unidade: null`, `_UNIT_INLINE` deteta unidade dominante no texto (≥80% das ocorrências) e aplica a todas. Fallback por pesquisa de string simples (EACH, UN, etc.) quando `_UNIT_INLINE` não encontra nada.
Resultado Integral Office: 100% correto em todos os campos e todas as 15 linhas ✅

## Fixes aplicados — fatura espanhola Garmin Iberia (2026-05-14)
Caso de teste: Garmin Iberia, S.A.U. → Norauto (1 artigo, I.V.A. 0%, NIF com prefixo CIF, unidade "Each").
- **Fix 7 — fornecedor_nif sem prefixo de país:** modelo extraía "A-08829699" (CIF espanhol sem prefixo ES). `_VAT_LINE` deteta "VAT - ESA08829699" e substitui pelo NIF completo com país.
- **Fix 8 — referencia com número de linha:** modelo prefixava a referência com o número da linha ("1 010-11838-00"). Quando texto tem coluna "Línea", o prefixo numérico é removido.
- **Fix 9 — markdown fences / texto extra no output do modelo:** modelo ocasionalmente emite texto antes do JSON ou envolve em \`\`\`json...\`\`\`. `_ollama_request` agora extrai o bloco JSON deterministicamente: strip de fences + `brace = stripped.find("{")`.
Resultado Garmin Iberia: 100% correto em todos os campos ✅

## Fixes aplicados — fatura ES Ascendeo Iberia / MAG / layout com códigos secundários (2026-05-15)
Caso de teste: Ascendeo Iberia SL → Norauto Barreiro (6 artigos, Exempt from VAT, layout com códigos secundários numéricos entre linhas, loja identificada por "NORAUTO BARREIRO (MAG 615)" em OBSERVACIONES).
- **Fix 11 estendido — loja_armazem via "NORAUTO XXX (MAG NNN)":** regex `_LOJA_MAG` adicionado como fallback quando `_ARMAZEM` não encontra nada. Extrai "NORAUTO BARREIRO (MAG 615)" de "MATERIAL PARA NORAUTO BARREIRO (MAG 615)".
- **Fix 12 — referencia numérica corrigida:** layout tem códigos secundários (684808, 684801...) em linhas isoladas entre artigos. Modelo usa o código secundário como referência do artigo seguinte. Fix deteta referências puramente numéricas de 5-8 dígitos e procura o código alfanumérico do artigo na linha imediatamente seguinte no texto.
- **Fix 13 — quantidade inconsistente:** modelo extraía `quantidade: 1` para MUWPC0008 quando o correto era 3 (3 × 8,75 = 26,25). Fix verifica `preco_unitario × quantidade ≈ total` quando não há desconto; se inconsistente e `total/preco_unitario` é inteiro, corrige a quantidade.
Resultado Ascendeo Iberia: 100% correto em todos os campos e 6 linhas ✅

## Fixes aplicados — fatura EU TNT Exportacion / ARMAZEM (2026-05-15)
Caso de teste: TNT EXPORTACION → Norauto (2 linhas, V.A.T. 0 %, loja_armazem "ARMAZEM 11", linha com 100% desconto, "Agency" como label do fornecedor, número extra abaixo de "INVOICE" que confunde o modelo).
- **Fix 2 atualizado — V.A.T. com espaço:** padrão anterior exigia `V\.A\.T\.\s*:\s*0` (com dois pontos). Texto tinha `V.A.T. 0 %` (sem dois pontos, só espaço). Padrão alargado para `V\.A\.T\.\s*[:\s]\s*0`.
- **Fix 3 atualizado — fornecedor_pais:** condição alargada de "nome E nif ambos null" para "nif null". Sem NIF não é possível confirmar o país — o texto pode conter o país do comprador (ex: "Portugal" no endereço da Norauto).
- **Fix 0c — fornecedor_nome de "Agency\nNOME":** modelo não extraía nome quando label é "Agency" em vez de "Supplier:". `_AGENCY_NAME` regex deteta e preenche quando `fornecedor_nome` é null.
- **Fix 4b — numero_fatura de "Invoice nbr.":** documento tinha "03268" logo abaixo de "INVOICE" (número de encomenda) e "EU130203" na linha seguinte ao cabeçalho "Invoice nbr.". Modelo confundiu e extraía "03268". `_INVOICE_NBR_LINE` extrai deterministicamente e tem precedência sobre o que o modelo retornou.
- **Fix 10 — linha com desconto 100%:** modelo extraía `total: 74.4` (correto pela tabela) mas o desconto de 100% indicava que o valor devia ser 0,00. Dois casos: Caso A — modelo extraiu `desconto: "100"` mas calculou total errado; Caso B — modelo extraiu `desconto: null`, fix deteta padrão `REF...100 0,00` no texto e corrige.
- **Fix 11 — loja_armazem via "ARMAZEM N":** modelo não extraía quando o padrão era "ARMAZEM 11" sem label explícita. `_ARMAZEM` regex deteta deterministicamente quando `loja_armazem` é null.
Resultado TNT Exportacion: 100% correto em todos os campos ✅

## Fixes aplicados — fatura ES Ascendeo Iberia / 10 artigos / refs numéricas (2026-05-16)
Caso de teste: Ascendeo Iberia → Norauto (10 artigos, 2 com ref numérica 9901009 e 9909041, OBSERVACIONES vazio, sem loja).
- **Fix 12 Caso B estendido:** Fix 12 anterior só detetava código alfanumérico após código secundário. Este documento tem artigos com ref numérica (9901009, 9909041) após o código secundário. Adicionado Caso B: quando `ref numérica\n` é seguida de `\d+[ \t]+\S` (número + texto na mesma linha), extrai o número como referência correta.
- **Fix 14 — prefixo de referência na descrição:** modelo incluía o código de artigo no início da descrição (ex: "MUDCC0091 Cargador..."). Fix remove o prefixo quando `descricao.startswith(referencia + " ")`.
Resultado (texto simplificado): 10/10 campos corretos ✅

## Fix aplicado — linhas fantasma nulas + rollback exemplo 32 (2026-05-16)
Causa: adicionar um 2º exemplo de treino para o mesmo formato Ascendeo causava linhas nulas (referencia preenchida mas descricao/quantidade/total todos null) intercaladas com as linhas reais.
- **Fix 15 — remove linhas fantasma nulas:** filtra linhas onde `referencia` está preenchida mas `descricao`, `quantidade` e `total` são todos null.
- **Fix 15 estendido:** também remove linhas onde ref é só letras maiúsculas (ex: "ALBARAN", "PEDIDO") com desc=null e qty=null, mesmo que total não seja null.
- **Rollback do exemplo 32:** Modelfile revertido para 31 exemplos (removido o par Ascendeo 980.376 F que causava regressão). Os fixes 12 e 14 são suficientes para este formato.

## Fixes aplicados — fatura ES Ascendeo 980.376 F / PDF real (2026-05-16 / 2026-05-17)
Caso de teste real: Ascendeo Iberia SL → Norauto Portugal (10 artigos, formato "REF DESC QTY UN PRICE DTO IMPORTE", layout com CLIENTE em destaque, códigos secundários numéricos entre artigos, linha de metadata "ALBARAN :", símbolo `"` na descrição de MUCHL0009).

**1ª submissão** — modelo confundiu NORAUTO como fornecedor (CLIENTE aparece primeiro no texto), gerou linha phantom ALBARAN, saltou MUCHL0009 (`"` quebrou geração de JSON), incluiu 9 códigos secundários como linhas de artigo:
- **normalize_doc_type — sanitização de `"`:** `(\d)"` → `$1''` resolve MUCHL0009.
- **Fix 0a:** sobrescreve fornecedor errado com "Ascendeo Iberia SL is registered for VAT no. ES B60502333".
- **Fix 15 estendido:** remove linha ALBARAN (ref all-uppercase + desc=null + qty=null).
- **Fix 16 — deduplicar refs repetidas:** mantém entrada com desc preenchida ou maior total.
- **Fix 17 — refs numéricas orfãs:** remove 9 códigos secundários (805056, 917488, 805043, 906873, 944792, 684801, 653697, 940415, 805053) — 6 dígitos, sem desc, isolados no texto sem texto na mesma linha (`[ \t]+` não cruza newlines).

**2ª submissão** — MUCHL0009 recuperado, mas modelo "travou" num preço anterior (4.85) para 9901009/MUARM0031/MUDAP0003; 9909041 tinha desc=null:
- **Fix 18 — extrair desc do texto (2 padrões):** Padrão A: "REF DESC QTY UN PRICE DTO TOTAL" (Ascendeo/ES, primário); Padrão B: "REF DESC QTY PRICE UN" (fallback). Extrai desc para 9909041.
- **Fix 19 — corrigir qty/preco/total errados:** compara total do modelo com total do texto via padrão "QTY UN PRICE DTO TOTAL"; se diverge >0.01, corrige qty+preco_unitario+total. Corrigiu 9901009 (1→4, 4.85→19.40), MUARM0031 (1→2, 4.85/4.85→9.73/19.46), MUDAP0003 (1→2, 4.85→6.80/13.60).

Resultado: 10/10 linhas corretas, cabeçalho 100% correto ✅

## Fix aplicado — loja_armazem truncada / paren omitido (2026-05-17)
Caso de teste: Ascendeo Iberia → Norauto Guimarães (8 artigos, fatura 898.403 F).
pdfplumber truncava a linha "NORAUTO GUIMARAES (MAG 616)" → "NORAUTO GUIMARAES (MAG 616" (sem `)`) em OBSERVACIONES.
- **_LOJA_MAG regex — `)` opcional:** `\(MAG\s+\d+\)` → `\(MAG\s+\d+\)?`. Se o `)` estiver ausente na captura, é adicionado no código antes de gravar `loja_armazem`.
Resultado final 898.403 F (2026-05-31): `numero_fatura: "898.403 F"`, `loja_armazem: "NORAUTO GUIMARAES (MAG 616)"`, `imposto_taxa: 0`, `valor_total: 243.25`, `valor_liquido: 243.25`, `unidade: "UN"` em todas as linhas, `"Tamaño"` correto, 8/8 linhas sem códigos secundários ✅
**Nota:** na verificação intermédia, a app mostrava valores null apesar dos fixes — diagnosticado como cache do Streamlit (mesmo PDF re-submetido devolve resultado em cache). Solução: reiniciar a app. Ver secção "Cache do Streamlit" abaixo.

## Fixes aplicados — fatura ES 898.403 F / backslash no output / linhas incompletas (2026-05-26)
Caso de teste: Integral Office Comunicaciones SLU → Norauto Guimarães (8 artigos, 898.403 F).
Sequência de submissões após restart:

**Submissão com `\` no output do modelo** — modelo emitia `\` antes de cada newline (`"fatura",\`), JSON inválido, `json.loads` falhava, `_sanitize_result` recebia str e saía sem aplicar nenhum fix. Todos os campos corrigíveis (numero_fatura, imposto_taxa, valor_total) permaneciam errados.
- **`_ollama_request` — strip de `\` antes de newlines:** `re.sub(r'\\\s*\n', '\n', stripped)` antes de `json.loads`. Resolve silenciosamente sem regressões.

**Submissão com linhas incompletas** — modelo gerou CYEFM032 e 9908405 com `tipo: null`, `quantidade: null` (geração JSON cortada a meio de campos).
- **Fix 20 — estendido para completar linhas incompletas:** além de adicionar linhas em falta, agora também preenche campos críticos nulos (tipo, quantidade, preco_unitario, total, unidade) em linhas já presentes mas incompletas.

**Submissão com valor_total = 898.403** — modelo confundiu número de fatura (898.403 F) com valor_total.
- **Fix 21 — valor_total de "TOTAL . . . : NNN,NN":** extrai o total real do texto; se difere do modelo em >0.01, substitui. Também corrige valor_liquido se imposto_taxa=0.

## Cache do Streamlit — fixes que parecem não aplicar (descoberta 2026-05-20)
Quando o mesmo ficheiro PDF é submetido novamente sem reiniciar a app, o Streamlit pode devolver o resultado anterior em cache — os fixes estão no código mas o output exibido é idêntico à submissão anterior.

**Sintoma:** JSON na UI mostra os mesmos valores null depois de adicionar um fix.
**Diagnóstico:** se `normalize_doc_type` já estava a correr (ex: `7''` visível em vez de `7"`) mas os campos continuam null → é cache, não código.
**Solução:** reiniciar a app: `python -m streamlit run C:\projetos\Agente\app.py`

Diferente do problema de Fix 9 (modelo emitia texto extra → `_sanitize_result` recebia str): esse era silencioso e afetava todos os fixes; o cache afeta apenas re-submissões da mesma sessão.

## Causa raiz dos fixes que "não funcionavam" (descoberta 2026-05-14)
`_sanitize_result` recebia uma `str` em vez de `dict` porque `json.loads` falhava quando o modelo emitia texto extra antes/depois do JSON. O check `if not isinstance(result, dict): return result` saía imediatamente sem aplicar nenhum fix. O JSON era exibido corretamente via `st.code(result, language="json")` tornando o problema invisível. Fix 9 resolve globalmente.

## Caso de teste documentado — pior caso (AstorGes / Fatura Simplificada)
PDF com tabela gráfica ~10 colunas, QR Code vetorial (não detetável por cv2), página rodada.
- **QR Code:** não extraível — AstorGes gera QR vetorial, sem URI de anotação no PDF
- **OCR:** PSM 11 testado, sem melhoria; pdfplumber falha por linhas com "=" → fallback Tesseract 400 DPI
- **Campos corretos:** tipo_documento, numero_fatura, data_emissao, valor_liquido, imposto_taxa, imposto_valor, valor_total, fornecedor_nome (null), fornecedor_nif (null), linhas[].total, linhas[].referencia (null quando ilegível) ✅
- **Campos recuperados parcialmente:** linhas[].descricao — "Artigo (descrição ilegível por OCR)" quando impossível ler; "Bateria Usada 12V" quando OCR parcialmente legível
- **Conclusão:** adequado para lançamento contabilístico automático (todos os valores monetários corretos, referencias honestamente null em vez de garbled)

## Contexto adicional
- Empresa tem várias lojas e armazéns — campo `loja_armazem` obrigatório quando mencionado
- Faturas nacionais e estrangeiras (PT, EU, UK, US, etc.)
- Integração futura com PHC (já existe PHC Simulator em `C:/projetos/4. hiperfrio-o2c-rag`)
- Futuro: n8n/make.com para automação email → Faturix → PHC/Moloni
- Futuro: servidor MCP para integração com software de faturação
