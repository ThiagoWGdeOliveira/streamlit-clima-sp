import pandas as pd
import numpy as np
import geopandas as gpd # type: ignore
import json
import requests
import matplotlib.pyplot as plt
import seaborn as sns
import streamlit as st # type: ignore
import folium
import os
from streamlit_folium import st_folium # type: ignore
from datetime import datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go
import matplotlib.colors as mcolors

# shapefile_municipios - seleciona os shapes dos municípios com a api do ibge
# municipios_por_estado - seleciona o dataframe com os códigos do município com a api do ibge
# obter_dados_climaticos - obtem dados climáticos diários da api do NASAPOWER - precipitação e temperaturas
# baixar_dados_climaticos_nasa_power - baixa os dados climáticos para cada município entre um intervalo de dados, e função {obter_dados_climaticos} é usada dentro dessa função
# agregar_dados_climaticos - agrega os dados climáticos para o(s) municípios selecionados
# limpeza_dos_dados - limpa os valores NaN com preenchimento do valor anterior ou posterior
# salvar_ou_atualizar_dados - salva ou atualiza a base de dados, ou seja, sempre que rodar ele não irá baixar todos os dados novamente, apenas os valores que faltam do intervalo de datas

@st.cache_data
def shapefile_municipios(uf):
    url = f"https://servicodados.ibge.gov.br/api/v4/malhas/estados/{uf}?formato=application/json&intrarregiao=Municipio&qualidade=intermediaria"
    response = requests.get(url) 
    if response.status_code == 200: #  Se for 200, significa que os dados foram baixados corretamente
        municipios  = gpd.read_file(response.text)
        return municipios
    else:
        print("Erro:", response.status_code, response.text)

@st.cache_data
def municipios_por_estado(uf: str):
    url = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
    response = requests.get(url)
    if response.status_code == 200:
        dados = response.json()
        municipios = [{
            'codigo_ibge': mun['id'],
            'municipio': mun['nome'],
            'uf': uf.upper()
        } for mun in dados]
        return pd.DataFrame(municipios)
    else:
        print(f"Erro {response.status_code}: {response.text}")
        return pd.DataFrame()

@st.cache_data
def obter_dados_climaticos(long_x, lat_y, start_date, end_date):
    variavel = 'PRECTOTCORR,T2M,T2M_MAX,T2M_MIN'
    endpoint_nasa_power = f"https://power.larc.nasa.gov/api/temporal/daily/point?parameters={variavel}&community=SB&longitude={long_x}&latitude={lat_y}&start={start_date}&end={end_date}&format=JSON"
    req_power = requests.get(endpoint_nasa_power).content
    json_power = json.loads(req_power)
    df = pd.DataFrame(json_power['properties']['parameter'])
    df.rename(columns = {'PRECTOTCORR':'prec','T2M':'temp','T2M_MAX':'temp_max', 'T2M_MIN':'temp_min'},inplace=True)
    df.index = pd.to_datetime(df.index)
    df['month'] = df.index.month
    df['year'] = df.index.year
    return df

@st.cache_data
def baixar_dados_climaticos_nasa_power(_df_municipios, start, end):
    todos_dados = []
    for _, row in _df_municipios.iterrows():
        try:
            df_clima = obter_dados_climaticos(row['long_x'], row['lat_y'], start, end)
            if df_clima is not None:
                df_clima['municipio'] = row['municipio']
                df_clima['codigo_ibge'] = row['codigo_ibge']
                todos_dados.append(df_clima)
        except Exception as e:
            print(f"Erro com municipio {row['municipio']}: {e}")
    df_completo = pd.concat(todos_dados)
    return df_completo


@st.cache_data
def limpeza_dos_dados(df):
    colunas = ['prec', 'temp', 'temp_max', 'temp_min']
    for col in colunas:
        df[col] = df[col].replace(-999.0, np.nan)
        # Interpolação por município
        df[col] = df.groupby('municipio')[col].transform(
            lambda x: x.ffill().bfill()
        )
    return df

def salvar_ou_atualizar_dados(df_municipios, caminho, start_fixo):
    """
    Atualiza ou cria um arquivo local com dados climáticos por município, salvando incrementalmente as novas datas.
    """
    if os.path.exists(caminho):
        df_existente = pd.read_parquet(caminho)
        ultima_data = pd.to_datetime(df_existente.index).max().date()
        nova_data_inicio = (ultima_data + timedelta(days=1)).strftime("%Y%m%d")
    else:
        df_existente = pd.DataFrame()
        nova_data_inicio = start_fixo
        print('Arquivo não encontrado. Iniciando novo download.')
    # Data final: hoje menos 5 dias
    data_final = (datetime.today() - timedelta(days=5)).strftime("%Y%m%d")
    # Baixar novos dados se houver novas datas
    if nova_data_inicio <= data_final:
        df_novo = baixar_dados_climaticos_nasa_power(df_municipios, nova_data_inicio, data_final)
        df_novo = limpeza_dos_dados(df_novo)
        # Concatenar com os dados existentes
        df_final = pd.concat([df_existente, df_novo]).drop_duplicates()
        df_final.to_parquet(caminho, index=True)
        print('Dados atualizados e salvos com sucesso')
    else:
        df_final = df_existente
        print('Nenhuma atualização necessária.')
    return df_final


@st.cache_data
def agregar_dados_climaticos(df, lista_municipios=None):
    if lista_municipios and lista_municipios != 'Todos':
        df = df[df['municipio']==lista_municipios]
        return df.groupby(['year', 'month']).agg({
            'prec':'sum',
            'temp':'mean',
            'temp_max':'mean',
            'temp_min':'mean'
            }).reset_index()
    else:
        df_agg = df.groupby(['year', 'month', 'municipio']).agg({
            'prec':'sum',
            'temp':'mean',
            'temp_max':'mean',
            'temp_min':'mean'
            }).reset_index()
        df_media_estado = df_agg.groupby(['year', 'month']).agg({
            'prec':'mean',
            'temp':'mean',
            'temp_max':'mean',
            'temp_min':'mean'
            }).reset_index()
        return df_media_estado


st.set_page_config(page_title = 'Analise Climatica',
                   layout='wide',
                   initial_sidebar_state='expanded')

st.title("Dados de 🌡️ Temperatura (°C) e de 🌧️ Precipitação (mm) por município do estado de São Paulo")
st.markdown("---")

# Variáveis de data
ano_inicio = datetime.today().year-8
start_date = (datetime(ano_inicio,1,1).date()).strftime("%Y%m%d")
end_date = ((datetime.today() - timedelta(days=5)).date()).strftime("%Y%m%d")

# Variáveis shape
# Parâmetros
uf = 'SP'
# Obter o shapefile
gdf = shapefile_municipios(uf)
# Setar o CRS
gdf = gdf.set_crs(epsg=4674)
# Municípios
df_mun = municipios_por_estado(uf)
df_mun['codigo_ibge'] = df_mun['codigo_ibge'].astype(str)
# Join nas duas bases
gdf = pd.merge(gdf, df_mun, left_on='codarea', right_on='codigo_ibge')
gdf['long_x'] = gdf.geometry.centroid.x
gdf['lat_y'] = gdf.geometry.centroid.y
# Baixar dados climáticos
caminho ='dados_SP/dados_climaticos_SP.parquet'
df_completo = salvar_ou_atualizar_dados(gdf, caminho, start_fixo=start_date)


st.sidebar.header('Estado de São Paulo', divider='gray')
st.sidebar.subheader('🔽 Filtros por Município e Data', divider='gray')

opcoes_selecao = ['Todos']+sorted(df_completo['municipio'].unique())
lista_municipios = st.sidebar.selectbox('🏙️ Municípios', opcoes_selecao)

data_range = st.sidebar.date_input(
    "📆 Selecione o período de análise",
    value=(pd.to_datetime(start_date), pd.to_datetime(end_date)),
    min_value=pd.to_datetime(start_date),
    max_value=pd.to_datetime(end_date),
)

if isinstance(data_range, tuple) and len(data_range) == 2:
    start, end = data_range
else:
    st.warning("Por favor, seleciona uma data inicial e uma data final")
    st.stop()
    #start = end = data_range

start = pd.to_datetime(start)
end = pd.to_datetime(end)

df_real = df_completo.copy()
df_real = df_real[(df_real.index >= start) & (df_real.index <= end)]

tab1, tab2, tab3 = st.tabs(['Descrição', 'Precipitação (mm)', 'Temperatura média, máxima e mínima (°C)'])

with tab1:
    st.write('Os dados utilizados são de precipitação e temperatura diária do NASA POWER.')
    st.write('Foi utilizado os shapefiles do IBGE para extrair o centróide de cada município do estado de São Paulo.')
    st.write('Com as coordenadas (latitude e longitude), foi extraido os dados climáticos do período atual (com uma defasagem de 5 dias) até primeiro de Janeiro de 8 anos atrás. *Por exemplo, se o ano atual é 2025, os dados mais antigos carregados começarão em 01-01-2017 (2025-8)*.')
    st.write('Os gráficos apresentados foram agrupados por mês em cada ano.')
    st.write('Com a seleção do município temos o um mapa interativo do folium para destacar a área escolhida.')


with tab2:
    if lista_municipios != 'Todos':
        gdf_filtrado = gdf[gdf['municipio']==lista_municipios]
        df_filtrado = df_real[df_real['municipio']==lista_municipios]
    else:
        gdf_filtrado = gdf
        df_filtrado = df_real
    
    st.write("Período filtrado:", df_filtrado.index.min(), "até", df_filtrado.index.max())
    
    centro_lat = gdf_filtrado['lat_y'].mean()
    centro_lon = gdf_filtrado['long_x'].mean()
    bounds = gdf_filtrado.total_bounds
    m = folium.Map()
    m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])


    folium.GeoJson(
        gdf_filtrado,
        name='Município' if lista_municipios else 'Estado de SP',
        style_function=lambda x:{
            'fillColor':'#ff9999',
            'color':'black',
            'weight':0.7,
            'fillOpacity':0.4
        },
        tooltip=folium.GeoJsonTooltip(fields=['municipio'], aliases=['Município:'])
    ).add_to(m)

    st_data = st_folium(m, width=800, height=400, key='mapa_precipitacao')
    
    df_filtrado['data'] = df_filtrado.index
    dias_unicos = df_filtrado['data'].nunique()
    chuva_baixa = df_filtrado[df_filtrado['prec'] < 0.5]['data'].nunique()

    if lista_municipios != 'Todos':
        prec_media_anual = df_filtrado.groupby('year')['prec'].sum().mean()
    else:
        prec_por_mun = df_filtrado.groupby(['municipio', 'year'])['prec'].sum().reset_index()
        prec_media_anual = prec_por_mun.groupby('year')['prec'].mean().mean()

    col1, col2, col3 = st.columns(3)
    col1.metric(" Total de Dias ", f'{dias_unicos}')
    col2.metric("🌧️ Dias com chuva menor que 0,5 mm", f"{chuva_baixa}")
    col3.metric("📆 Precipitação média anual", f"{prec_media_anual:.0f} mm")

    df_filtrado = agregar_dados_climaticos(df_filtrado, lista_municipios = lista_municipios)

    fig = px.line(
        df_filtrado, x="month", y="prec", color="year",
        markers=True, 
        title="Precipitação Mensal por Ano",
        labels={"month": "Mês", "prec": "Precipitação", "year": "Ano"},
        color_discrete_sequence=px.colors.sequential.Blues_r if df_filtrado['year'].nunique() <= 3 else px.colors.sequential.Blues)
    fig.update_layout(
        xaxis=dict(title="Mês",
            tickmode="array",
            tickvals=list(range(1, 13)),  # Posições dos meses
            ticktext=["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]  # Nomes dos meses
        ),
        yaxis=dict(title="Precipitação (mm)"),
        legend_title="Ano",
        template="plotly_white"
    )

    st.plotly_chart(fig)


with tab3:
    if lista_municipios != 'Todos':
        gdf_filtrado = gdf[gdf['municipio']==lista_municipios]
        df_filtrado = df_real[df_real['municipio']==lista_municipios]
    else:
        gdf_filtrado = gdf
        df_filtrado = df_real
    
    st.write("Período filtrado:", df_filtrado.index.min(), "até", df_filtrado.index.max())

    centro_lat = gdf_filtrado['lat_y'].mean()
    centro_lon = gdf_filtrado['long_x'].mean()
    bounds = gdf_filtrado.total_bounds
    m1 = folium.Map()
    m1.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

    folium.GeoJson(
        gdf_filtrado,
        name='Município' if lista_municipios else 'Estado de SP',
        style_function=lambda x:{
            'fillColor':'#ff9999',
            'color':'black',
            'weight':0.7,
            'fillOpacity':0.4
        },
        tooltip=folium.GeoJsonTooltip(fields=['municipio'], aliases=['Município:'])
    ).add_to(m1)

    st_data2 = st_folium(m1, width=800, height=400, key='mapa_temperatura')

    df_filtrado['data'] = df_filtrado.index
    dias_unicos = df_filtrado['data'].nunique()
    dias_quentes = df_filtrado[df_filtrado['temp_max']>35]['data'].nunique()
    dias_frios = df_filtrado[df_filtrado['temp_min']<5]['data'].nunique()

    col1, col2, col3 = st.columns(3)
    col1.metric(" Total de Dias ", f'{dias_unicos}')
    col2.metric("🔥 Dias com T. Máx > 35 °C", f"{dias_quentes}")
    col3.metric("❄️ Dias com T. Mín < 5 °C", f"{dias_frios}")

    df_filtrado = agregar_dados_climaticos(df_filtrado, lista_municipios = lista_municipios)

    fig4 = px.line(
        df_filtrado, x="month", y="temp", color="year",
        markers=True, 
        title="Temperatura média por Ano",
        labels={"month": "Mês", "temp": "Temp. Média", "year": "Ano"},
        color_discrete_sequence=px.colors.sequential.YlOrBr_r if df_filtrado['year'].nunique() <= 3 else px.colors.sequential.YlOrBr)
    fig4.update_layout(
        xaxis=dict(title="Mês",
            tickmode="array",
            tickvals=list(range(1, 13)),  # Posições dos meses
            ticktext=["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]  # Nomes dos meses
        ),
        yaxis=dict(title="Temp. Média (°C)"),
        legend_title="Ano",
        template="plotly_white"
    )

    st.plotly_chart(fig4)

    fig2 = px.line(
        df_filtrado, x="month", y="temp_max", color="year",
        markers=True, 
        title="Temperatura máxima por Ano",
        labels={"month": "Mês", "temp_max": "Temp. Máxima", "year": "Ano"},
        color_discrete_sequence=px.colors.sequential.OrRd_r if df_filtrado['year'].nunique() <= 3 else px.colors.sequential.OrRd)
    fig2.update_layout(
        xaxis=dict(title="Mês",
            tickmode="array",
            tickvals=list(range(1, 13)),  # Posições dos meses
            ticktext=["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]  # Nomes dos meses
        ),
        yaxis=dict(title="Temp. Máxima (°C)"),
        legend_title="Ano",
        template="plotly_white"
    )

    st.plotly_chart(fig2)

    fig3 = px.line(
        df_filtrado, x="month", y="temp_min", color="year",
        markers=True, 
        title="Temperatura mínima por Ano",
        labels={"month": "Mês", "temp_min": "Temp. Mínima", "year": "Ano"},
        color_discrete_sequence=px.colors.sequential.Purples_r if df_filtrado['year'].nunique() <= 3 else px.colors.sequential.Purples)
    fig3.update_layout(
        xaxis=dict(title="Mês",
            tickmode="array",
            tickvals=list(range(1, 13)),  # Posições dos meses
            ticktext=["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]  # Nomes dos meses
        ),
        yaxis=dict(title="Temp. Mínima (°C)"),
        legend_title="Ano",
        template="plotly_white"
    )

    st.plotly_chart(fig3)

st.markdown("---")
st.caption("🚀 Projeto desenvolvido por [Thiago WG de Oliveira](https://github.com/ThiagoWGdeOliveira) · Análise Climática de São Paulo com Streamlit")