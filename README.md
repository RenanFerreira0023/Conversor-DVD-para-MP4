# Conversor DVD/VHS para MP4

Projeto em Python para assistir, testar, reparar e converter filmagens antigas copiadas de VHS/DVD.

Ele trabalha principalmente com arquivos `.VOB`, que sao comuns em DVDs antigos e normalmente ficam dentro da pasta `VIDEO_TS`.

## Executaveis do projeto

### `media_player.py`

Abre uma interface grafica para reproduzir videos antigos usando o motor do VLC.

Use este script quando quiser assistir ou conferir arquivos antes/depois da conversao.

O que ele faz:

- abre arquivos `.VOB`, `.MPG`, `.MPEG`, `.MP4`, `.AVI`, `.MKV`, `.MOV` e `.WMV`;
- abre uma pasta de DVD ou uma pasta `VIDEO_TS`;
- monta uma lista com os segmentos de video do DVD;
- usa o VLC para decodificar MPEG-2 e formatos antigos;
- mostra controles de play/pause, avancar/voltar, volume e tempo;
- tenta detectar duracao com `ffprobe` quando disponivel;
- avisa quando a pasta tem `.IFO`/`.BUP`, mas nao tem `.VOB`.

Como rodar:

```powershell
python media_player.py
```

Dependencias principais:

- VLC Media Player instalado no Windows;
- pacote Python `python-vlc`;
- FFmpeg/ffprobe opcional para melhorar a deteccao de duracao.

### `dvd_vob_batch_converter.py`

Procura arquivos `.VOB` dentro de uma pasta raiz e converte cada um para `.mp4`.

Use este script quando quiser converter muitos DVDs ou muitas pastas de uma vez.

O que ele faz:

- busca recursivamente todas as pastas que contem arquivos `.VOB`;
- testa cada `.VOB` por alguns segundos antes de converter;
- gera um `.mp4` com o mesmo nome do `.VOB`;
- pula arquivos que ja possuem um `.mp4` correspondente;
- mostra estimativa de tempo durante o processamento;
- cria um relatorio chamado `relatorio_conversao_dvds.txt`;
- pode rodar com interface grafica ou por linha de comando.

Como rodar com interface grafica:

```powershell
python dvd_vob_batch_converter.py
```

Como rodar por linha de comando:

```powershell
python dvd_vob_batch_converter.py "C:\caminho\para\os\dvds"
```

Exemplo apenas para mapear, sem converter:

```powershell
python dvd_vob_batch_converter.py "C:\caminho\para\os\dvds" --dry-run
```

Exemplo mudando o tempo de teste:

```powershell
python dvd_vob_batch_converter.py "C:\caminho\para\os\dvds" --test-seconds 15
```

Dependencia principal:

- FFmpeg instalado e disponivel no `PATH`.

### `repair_dvd_video.py`

Une e reencoda os `.VOB` de uma pasta `VIDEO_TS` em um unico arquivo `.mp4`.

Use este script quando um DVD antigo toca com tempo errado, busca quebrada, segmentos separados ou problemas de reproducao.

O que ele faz:

- localiza os `.VOB` principais dentro da pasta `VIDEO_TS`;
- ignora o `VIDEO_TS.VOB` quando ele nao e o filme principal;
- concatena os segmentos do DVD;
- reencoda o video para H.264 e o audio para AAC;
- gera um MP4 mais facil de abrir em players modernos;
- cria a pasta de saida automaticamente se necessario.

Como rodar usando a pasta `VIDEO_TS` do diretorio atual:

```powershell
python repair_dvd_video.py
```

Como informar uma pasta especifica:

```powershell
python repair_dvd_video.py "C:\caminho\para\DVD\VIDEO_TS"
```

Como escolher o nome do arquivo de saida:

```powershell
python repair_dvd_video.py "C:\caminho\para\DVD" -o "video_reparado.mp4"
```

Dependencia principal:

- FFmpeg instalado e disponivel no `PATH`.

## Como instalar

1. Instale o VLC Media Player para Windows:
   <https://www.videolan.org/vlc/>

2. Instale o FFmpeg para Windows e adicione a pasta `bin` ao `PATH`.

3. Instale a dependencia Python:

```powershell
pip install -r requirements.txt
```

## Importante sobre a pasta `VIDEO_TS`

Uma copia completa de DVD normalmente tem arquivos como:

- `VIDEO_TS.IFO`
- `VTS_01_0.IFO`
- `VTS_01_1.VOB`
- `VTS_01_2.VOB`

Os arquivos `.IFO` e `.BUP` sao indice/backup. O video e o audio ficam nos arquivos `.VOB`, que costumam ser bem maiores.

Se uma pasta tem apenas `.IFO` e `.BUP`, mas nao tem `.VOB`, ela provavelmente esta incompleta para reproducao ou conversao. Copie o DVD novamente incluindo todos os arquivos da pasta `VIDEO_TS`.
