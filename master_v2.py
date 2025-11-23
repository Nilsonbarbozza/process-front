"""
HTML Processor v2.0
Processador inteligente de HTML com extração semântica e otimização
"""
import sys
import os
import re
import base64
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from abc import ABC, abstractmethod
from lxml_html_clean import Cleaner

import requests
from bs4 import BeautifulSoup, Comment
import cssutils
from dotenv import load_dotenv
import magic
from readability import Document
import trafilatura

# Configurar logging do cssutils para silenciar warnings
cssutils.log.setLevel(logging.CRITICAL)

# ==============================
# CONFIGURAÇÃO
# ==============================
load_dotenv()

CONFIG = {
    'MAX_FILE_SIZE_MB': int(os.getenv('MAX_FILE_SIZE_MB', 10)),
    'MAX_IMAGE_SIZE_MB': int(os.getenv('MAX_IMAGE_SIZE_MB', 5)),
    'REQUEST_TIMEOUT': int(os.getenv('REQUEST_TIMEOUT', 10)),
    'OUTPUT_DIR': os.getenv('OUTPUT_DIR', 'componetes'),
}

OUT_DIR = CONFIG['OUTPUT_DIR']
STYLES_DIR = os.path.join(OUT_DIR, "styles")
IMAGES_DIR = os.path.join(OUT_DIR, "images")
STYLE_FILE = os.path.join(STYLES_DIR, "styles.css")

# ==============================
# LOGGING SETUP
# ==============================
def setup_logging():
    """Configura sistema de logging"""
    logger = logging.getLogger('html_processor')
    logger.setLevel(logging.DEBUG)
    
    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    
    # File handler
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)
    fh = logging.FileHandler('logs/processor.log')
    fh.setLevel(logging.DEBUG)
    
    # Formato
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    ch.setFormatter(formatter)
    fh.setFormatter(formatter)
    
    logger.addHandler(ch)
    logger.addHandler(fh)
    
    return logger

logger = setup_logging()

# ==============================
# UTILIDADES
# ==============================
def ensure_dirs():
    """Cria estrutura de diretórios"""
    Path(OUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(STYLES_DIR).mkdir(parents=True, exist_ok=True)
    Path(IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    logger.info(f"Diretórios criados: {OUT_DIR}")

def save_file(path: str, content: Any, mode: str = "w", is_bytes: bool = False):
    """Salva arquivo com tratamento de erros"""
    try:
        with open(path, mode + ("b" if is_bytes else ""), 
                  encoding=None if is_bytes else "utf-8") as f:
            f.write(content)
        logger.debug(f"Arquivo salvo: {path}")
    except Exception as e:
        logger.error(f"Erro ao salvar {path}: {e}")
        raise

def safe_b64decode(data: str) -> Optional[bytes]:
    """Decodificador seguro para base64"""
    try:
        data = data.strip()
        data = re.sub(r'[^A-Za-z0-9+/=]', '', data)
        missing_padding = len(data) % 4
        if missing_padding:
            data += '=' * (4 - missing_padding)
        return base64.b64decode(data)
    except Exception as e:
        logger.warning(f"Erro ao decodificar base64: {e}")
        return None

# ==============================
# VALIDAÇÃO
# ==============================
def validate_html_file(filepath: str) -> bool:
    """Valida arquivo HTML antes de processar"""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Arquivo não encontrado: {filepath}")
    
    # Validar tamanho
    size_mb = os.path.getsize(filepath) / (1024**2)
    if size_mb > CONFIG['MAX_FILE_SIZE_MB']:
        raise ValueError(f"Arquivo muito grande: {size_mb:.2f}MB (max: {CONFIG['MAX_FILE_SIZE_MB']}MB)")
    
    # Validar tipo MIME
    try:
        mime = magic.from_file(filepath, mime=True)
        valid_mimes = ['text/html', 'text/plain', 'application/xhtml+xml']
        if mime not in valid_mimes:
            logger.warning(f"Tipo MIME suspeito: {mime}, tentando processar mesmo assim")
    except Exception as e:
        logger.warning(f"Não foi possível verificar MIME: {e}")
    
    logger.info(f"Arquivo validado: {filepath} ({size_mb:.2f}MB)")
    return True

# ==============================
# DOWNLOAD SEGURO DE IMAGENS
# ==============================
def download_image_safe(url: str, max_size_mb: int = None) -> Optional[bytes]:
    """Download seguro de imagens com validações"""
    if max_size_mb is None:
        max_size_mb = CONFIG['MAX_IMAGE_SIZE_MB']
    
    headers = {'User-Agent': 'Mozilla/5.0 (HTML Processor Bot/2.0)'}
    
    try:
        response = requests.get(url, timeout=CONFIG['REQUEST_TIMEOUT'], 
                               stream=True, headers=headers)
        response.raise_for_status()
        
        # Verificar Content-Length
        size = int(response.headers.get('Content-Length', 0))
        if size > max_size_mb * 1024 * 1024:
            logger.warning(f"Imagem muito grande: {url} ({size/(1024**2):.2f}MB)")
            return None
        
        # Verificar Content-Type
        content_type = response.headers.get('Content-Type', '')
        if not content_type.startswith('image/'):
            logger.warning(f"Não é uma imagem: {url} ({content_type})")
            return None
        
        # Download em chunks
        content = b''
        for chunk in response.iter_content(chunk_size=8192):
            content += chunk
            if len(content) > max_size_mb * 1024 * 1024:
                logger.warning(f"Tamanho excedido durante download: {url}")
                return None
        
        logger.info(f"Imagem baixada: {url} ({len(content)/(1024):.2f}KB)")
        return content
    
    except requests.RequestException as e:
        logger.error(f"Erro ao baixar {url}: {e}")
        return None

# ==============================
# PROCESSADORES HTML
# ==============================
def clean_html(soup: BeautifulSoup) -> BeautifulSoup:
    """Limpa HTML de redundâncias"""
    # Remover comentários (exceto estruturais)
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        if not any(marker in str(c) for marker in ['HEADER', 'MAIN', 'FOOTER', 'SECTION']):
            c.extract()
    
    # Remover tags vazias
    for tag in soup.find_all(["div", "span", "p"]):
        if not tag.text.strip() and not tag.find_all():
            tag.decompose()
    
    # Remover scripts inline
    for script in soup.find_all("script"):
        if not script.get("src"):
            script.decompose()
    
    # Limpar meta tags desnecessárias
    for meta in soup.find_all("meta"):
        if not meta.get("charset") and not (meta.get("name") in ["viewport", "description"]):
            if not (meta.get("property") and meta.get("property").startswith("og:")):
                meta.decompose()
    
    # Normalizar tags
    for b in soup.find_all("b"):
        b.name = "strong"
    for i in soup.find_all("i"):
        i.name = "em"
    
    logger.info("HTML limpo e normalizado")
    return soup

def semantic_conversion(soup: BeautifulSoup) -> BeautifulSoup:
    """Converte divs em tags semânticas com IA básica"""
    conversions = 0
    
    for div in soup.find_all("div"):
        classes = ' '.join(div.get("class", [])).lower()
        div_id = (div.get("id") or "").lower()
        
        # Detectar padrões
        if any(keyword in classes or keyword in div_id 
               for keyword in ['header', 'top', 'masthead']):
            div.name = "header"
            conversions += 1
            
        elif any(keyword in classes or keyword in div_id 
                 for keyword in ['footer', 'bottom']):
            div.name = "footer"
            conversions += 1
            
        elif any(keyword in classes or keyword in div_id 
                 for keyword in ['main', 'content', 'primary']):
            div.name = "main"
            conversions += 1
            
        elif any(keyword in classes or keyword in div_id 
                 for keyword in ['section', 'block']):
            div.name = "section"
            conversions += 1
            
        elif any(keyword in classes or keyword in div_id 
                 for keyword in ['nav', 'menu', 'navigation']):
            div.name = "nav"
            conversions += 1
            
        elif any(keyword in classes or keyword in div_id 
                 for keyword in ['article', 'post', 'entry']):
            div.name = "article"
            conversions += 1
    
    logger.info(f"Conversão semântica: {conversions} tags convertidas")
    return soup

def extract_main_content(html: str) -> Optional[str]:
    """Extrai conteúdo principal usando Trafilatura e Readability"""
    try:
        # Tentar Trafilatura primeiro
        content = trafilatura.extract(html, 
                                     include_comments=False,
                                     include_tables=True,
                                     include_images=True,
                                     output_format='html')
        if content:
            logger.info("Conteúdo principal extraído com Trafilatura")
            return content
    except Exception as e:
        logger.warning(f"Trafilatura falhou: {e}")
    
    try:
        # Fallback para Readability
        doc = Document(html)
        content = doc.summary()
        logger.info("Conteúdo principal extraído com Readability")
        return content
    except Exception as e:
        logger.warning(f"Readability falhou: {e}")
    
    return None

def clean_head(soup: BeautifulSoup) -> BeautifulSoup:
    """Head minimalista: title + meta essenciais + CSS"""
    head = soup.head or soup.new_tag("head")
    
    # Preservar title
    title = head.find("title") or soup.new_tag("title")
    title.string = title.string or "Projeto"
    
    # Preservar meta importantes
    charset_meta = head.find("meta", charset=True)
    viewport_meta = head.find("meta", attrs={"name": "viewport"})
    description_meta = head.find("meta", attrs={"name": "description"})
    
    # Reconstruir head
    head.clear()
    head.append(soup.new_tag("meta", charset="utf-8"))
    head.append(title)
    
    if viewport_meta:
        head.append(viewport_meta)
    else:
        head.append(soup.new_tag("meta", attrs={
            "name": "viewport",
            "content": "width=device-width, initial-scale=1.0"
        }))
    
    if description_meta:
        head.append(description_meta)
    
    # Link CSS
    link_tag = soup.new_tag("link", rel="stylesheet", href="styles/styles.css")
    head.append(link_tag)
    
    soup.head = head
    logger.info("Head limpo e otimizado")
    return soup

def beautify_html(soup: BeautifulSoup, mode: str = "beautify") -> str:
    """HTML final legível ou minificado"""
    if mode == "beautify":
        html = soup.prettify(formatter="html")
    elif mode == "minify":
        html = soup.decode()
        html = re.sub(r"<!--(?!\s*HEADER|\s*MAIN|\s*FOOTER).*?-->", "", html, flags=re.DOTALL)
        html = re.sub(r">\s+<", "><", html)
        html = re.sub(r"\s{2,}", " ", html)
        html = re.sub(r"\n+", "", html)
        html = html.replace(' type="text/javascript"', '')
        html = html.replace(' type="text/css"', '')
        html = html.strip()
    else:
        html = soup.decode()
    
    logger.info(f"HTML formatado em modo: {mode}")
    return html

# ==============================
# PROCESSADORES CSS
# ==============================
def extract_style_tags(soup: BeautifulSoup) -> str:
    """Extrai <style> e inline styles"""
    combined_css = []
    seen_rules = set()

    # Extrair <style> tags
    for style_tag in soup.find_all("style"):
        if style_tag.string:
            combined_css.append(style_tag.string)
        style_tag.decompose()

    # Converter inline styles
    for el in soup.find_all(style=True):
        style_content = el["style"].strip()
        if not style_content:
            continue
        
        class_hash = hashlib.md5(style_content.encode()).hexdigest()[:8]
        class_name = f"inline_{class_hash}"
        el["class"] = (el.get("class") or []) + [class_name]
        del el["style"]
        
        rule = f".{class_name} {{{style_content}}}"
        if rule not in seen_rules:
            combined_css.append(rule)
            seen_rules.add(rule)

    css = "\n".join(combined_css)
    logger.info(f"CSS extraído: {len(combined_css)} regras")
    return css

def optimize_css(css_text: str) -> str:
    """Otimiza e minifica CSS"""
    try:
        sheet = cssutils.parseString(css_text)
        
        # Remover duplicatas e mesclar
        seen_rules = {}
        for rule in sheet:
            if rule.type == rule.STYLE_RULE:
                selector = rule.selectorText
                if selector in seen_rules:
                    # Mesclar propriedades
                    for prop in rule.style:
                        seen_rules[selector].style[prop.name] = prop.value
                else:
                    seen_rules[selector] = rule
        
        # Serializar minificado
        sheet.cssText = b''
        for rule in seen_rules.values():
            sheet.add(rule)
        
        optimized = sheet.cssText.decode('utf-8')
        logger.info("CSS otimizado e minificado")
        return optimized
    except Exception as e:
        logger.warning(f"Erro ao otimizar CSS: {e}, retornando original")
        return css_text

def extract_css_base64_images(css: str) -> str:
    """Extrai imagens base64 do CSS e salva"""
    pattern = re.compile(r'url\((data:image/[\w+]+;base64,[^)]+)\)')

    def repl(match):
        data_url = match.group(1)
        m = re.match(r'data:image/(\w+);base64,(.+)', data_url)
        if not m:
            return match.group(0)
        
        ext, b64_data = m.groups()
        filename = f"css_img_{hash(b64_data)}.{ext}"
        path = os.path.join(IMAGES_DIR, filename)
        
        if not os.path.exists(path):
            img_bytes = safe_b64decode(b64_data)
            if img_bytes:
                save_file(path, img_bytes, is_bytes=True)
                logger.debug(f"Imagem CSS extraída: {filename}")
        
        return f'url("../images/{filename}")'

    return pattern.sub(repl, css)

# ==============================
# PROCESSADOR DE IMAGENS
# ==============================
def extract_images(soup: BeautifulSoup) -> BeautifulSoup:
    """Extrai e baixa imagens"""
    img_count = 0
    
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if not src:
            continue

        # Imagens base64
        if src.startswith("data:image"):
            m = re.match(r'data:image/(\w+);base64,', src)
            ext = m.group(1) if m else "png"
            img_data = re.sub(r'data:image/\w+;base64,', '', src)
            img_bytes = safe_b64decode(img_data)

            if img_bytes:
                img_name = f"img_{hash(src)}.{ext}"
                img_path = os.path.join(IMAGES_DIR, img_name)
                save_file(img_path, img_bytes, is_bytes=True)
                img["src"] = f"images/{img_name}"
                img_count += 1

        # Imagens HTTP
        elif src.startswith("http"):
            content = download_image_safe(src)
            if content:
                ext = src.split(".")[-1].split("?")[0] or "png"
                ext = ext[:4]  # Limitar extensão
                img_name = f"img_{hash(src)}.{ext}"
                img_path = os.path.join(IMAGES_DIR, img_name)
                save_file(img_path, content, is_bytes=True)
                img["src"] = f"images/{img_name}"
                img_count += 1
    
    logger.info(f"Imagens processadas: {img_count}")
    return soup

# ==============================
# PIPELINE PATTERN
# ==============================
class ProcessorStage(ABC):
    """Estágio abstrato do pipeline"""
    @abstractmethod
    def process(self, context: Dict[str, Any]) -> Dict[str, Any]:
        pass

class ValidationStage(ProcessorStage):
    def process(self, context):
        logger.info("=== ETAPA 1: Validação ===")
        validate_html_file(context['input_file'])
        return context

class LoadingStage(ProcessorStage):
    def process(self, context):
        logger.info("=== ETAPA 2: Carregamento ===")
        with open(context['input_file'], "r", encoding="utf-8") as f:
            context['html'] = f.read()
        context['soup'] = BeautifulSoup(context['html'], "lxml")
        return context

class CleaningStage(ProcessorStage):
    def process(self, context):
        logger.info("=== ETAPA 3: Limpeza ===")
        context['soup'] = clean_html(context['soup'])
        context['soup'] = semantic_conversion(context['soup'])
        context['soup'] = clean_head(context['soup'])
        return context

class ExtractionStage(ProcessorStage):
    def process(self, context):
        logger.info("=== ETAPA 4: Extração de Recursos ===")
        context['css'] = extract_style_tags(context['soup'])
        context['css'] = extract_css_base64_images(context['css'])
        context['soup'] = extract_images(context['soup'])
        return context

class OptimizationStage(ProcessorStage):
    def process(self, context):
        logger.info("=== ETAPA 5: Otimização ===")
        context['css'] = optimize_css(context['css'])
        return context

class OutputStage(ProcessorStage):
    def process(self, context):
        logger.info("=== ETAPA 6: Geração de Saída ===")
        
        # Salvar CSS
        save_file(STYLE_FILE, context['css'])
        
        # Salvar HTML
        final_html = beautify_html(context['soup'], mode="beautify")
        save_file(f"{OUT_DIR}/index.html", final_html)
        
        # Relatório
        context['output'] = {
            'html_file': f"{OUT_DIR}/index.html",
            'css_file': STYLE_FILE,
            'images_dir': IMAGES_DIR
        }
        return context

class Pipeline:
    """Pipeline de processamento modular"""
    def __init__(self):
        self.stages: List[ProcessorStage] = []
    
    def add_stage(self, stage: ProcessorStage):
        self.stages.append(stage)
        return self
    
    def execute(self, initial_context: Dict[str, Any]) -> Dict[str, Any]:
        context = initial_context
        for i, stage in enumerate(self.stages, 1):
            try:
                context = stage.process(context)
            except Exception as e:
                logger.error(f"❌ Erro no estágio {stage.__class__.__name__}: {e}")
                raise
        return context

# ==============================
# MAIN
# ==============================
def main():
    if len(sys.argv) < 2:
        print("Uso: python master_v2.py <arquivo.html>")
        print("Exemplo: python master_v2.py page.html")
        sys.exit(1)

    html_file = sys.argv[1]
    
    print("🚀 HTML Processor v2.0")
    print("=" * 50)
    
    # Criar estrutura
    ensure_dirs()
    
    # Executar pipeline
    pipeline = Pipeline()
    pipeline.add_stage(ValidationStage()) \
            .add_stage(LoadingStage()) \
            .add_stage(CleaningStage()) \
            .add_stage(ExtractionStage()) \
            .add_stage(OptimizationStage()) \
            .add_stage(OutputStage())
    
    try:
        result = pipeline.execute({'input_file': html_file})
        
        print("\n✅ Processamento concluído com sucesso!")
        print("=" * 50)
        print(f"📁 Pasta de saída: {OUT_DIR}")
        print(f"📄 HTML: {result['output']['html_file']}")
        print(f"🎨 CSS: {result['output']['css_file']}")
        print(f"🖼️  Imagens: {result['output']['images_dir']}")
        print(f"📋 Log detalhado: logs/processor.log")
        print("=" * 50)
        
    except Exception as e:
        print(f"\n❌ Erro durante processamento: {e}")
        print("Verifique o arquivo logs/processor.log para detalhes")
        sys.exit(1)

if __name__ == "__main__":
    main()