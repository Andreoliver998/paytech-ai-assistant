# Changelog

## 2026-02-19

### Frontend
- Renderização automática de matemática (LaTeX) com KaTeX (inline e display).
- Normalização de blocos `$$ ... $$` e `\[ ... \]` antes do Markdown para evitar quebras e garantir auto-render.
- Estilos KaTeX ajustados para manter alinhamento/centralização e compatibilidade com dark mode.
- Correção do menu **Baixar conversa** (evita abrir/fechar instantaneamente por causa do handler global de click).
- Export de conversa: opção **Texto (.txt)** (client-side) + melhorias de download (fallback para abrir em nova aba quando necessário).
- Ajuste de tamanho da lupa no campo Search da sidebar.
- Normalização do token de autenticação no `localStorage` (remove aspas/`Bearer`, evita formato inválido) e UX melhor ao receber `401`.

### Como testar
- Math inline: `$c^2=a^2+b^2$`
- Math display: `$$ c^2 = a^2 + b^2 $$`
- Math sem delimitador (backend): `C = \\sqrt{6^2 + 8^2} = 10`
- Export: kebab da conversa → **Baixar conversa** → **Texto (.txt)** / **PDF** / **Word**
