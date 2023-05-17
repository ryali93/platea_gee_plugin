from bs4 import BeautifulSoup
import requests
import re
import json
import ee
import pandas as pd

ee.Initialize()

def get_credentials(url):
    req = requests.get(url) # "https://ryali93.users.earthengine.app/view/plateaapi"
    soup = BeautifulSoup(req.content, 'html.parser')
    data = soup.find_all('script')[3]
    match = re.search(r'{.*}', data.string)
    creds = json.loads(match.group(0))
    return creds

def create_query_ndvi_ndmi(lon_min, lat_min, lon_max, lat_max, start_date, end_date):
    # Cargar el conjunto de datos Sentinel-2
    sentinel2 = ee.ImageCollection('COPERNICUS/S2_SR') \
        .filterDate(start_date, end_date) \
        .filterBounds(ee.Geometry.Rectangle([lon_min, lat_min, lon_max, lat_max]))

    # Calcular el NDVI y el NDMI
    def calc_ndvi(image):
        ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI')
        return image.addBands(ndvi)

    def calc_ndmi(image):
        ndmi = image.normalizedDifference(['B8', 'B11']).rename('NDMI')
        return image.addBands(ndmi)

    # Calcular la media del NDVI y NDMI en la región
    def reduce_region(image):
        mean = image.reduceRegion(reducer=ee.Reducer.mean(), geometry=ee.Geometry.Rectangle([lon_min, lat_min, lon_max, lat_max]), scale=10)
        return image.set('NDVI', mean.get('NDVI')).set('NDMI', mean.get('NDMI'))

    sentinel2_ndvi_ndmi = sentinel2.map(calc_ndvi).map(calc_ndmi)
    sentinel2_ndvi_ndmi_mean = sentinel2_ndvi_ndmi.map(reduce_region).select(['NDVI', 'NDMI'])

    # Extraer la serie de tiempo de las medias de NDVI y NDMI en la región
    series = sentinel2_ndvi_ndmi_mean.filter(ee.Filter.notNull(['NDVI', 'NDMI'])).getRegion(ee.Geometry.Point(lon_min, lat_min), 10).getInfo()

    data = ee.String.encodeJSON(series)
    return data.serialize()

def create_query_flood(lon_min, lat_min, lon_max, lat_max, start_date, end_date):
    # Create AOI
    aoi = ee.Geometry.Rectangle([[lon_min, lat_min],[lon_max, lat_max]])

    # Define a default start date (preflood | during flood)
    start_date = [ee.Date(start_date), ee.Date(end_date)]
    advance_days = [60, 8]

    # Define a function to smoothen the raster and export the shapefile
    def getFloodShpUrl(floodLayer, value, radius, aoi, cellSize):
        # Define a boxcar or low-pass kernel.
        boxcar = ee.Kernel.square(radius, 'pixels', True)
        smooth_flood = floodLayer.eq(value).convolve(boxcar)
        smooth_flood_binary = smooth_flood.updateMask(smooth_flood.gt(0.5)).gt(0)
        vectors = smooth_flood_binary.reduceToVectors(
            geometry=aoi,
            crs=floodLayer.projection(),
            scale=cellSize,
            geometryType='polygon',
            eightConnected=False,
            labelProperty='zone',
            maxPixels=9e12
        )
        flood_vector = ee.FeatureCollection(vectors)
        return flood_vector

    def getFloodImage(s1_collection_t1, s1_collection_t2):
        zvv_thd = -3
        zvh_thd = -3
        pow_thd = 75

        z_iwasc = calc_zscore(s1_collection_t1, s1_collection_t2, 'IW')
        z = ee.ImageCollection.fromImages([z_iwasc]).sort('system:time_start')
        floods = mapFloods(z.mean(), zvv_thd, zvh_thd, pow_thd)
        return floods.clip(aoi)

    def getSentinel1WithinDateRange(date, span):
        filters = [
            ee.Filter.listContains("transmitterReceiverPolarisation", "VV"),
            ee.Filter.listContains("transmitterReceiverPolarisation", "VH"),
            ee.Filter.Or(
                ee.Filter.equals("instrumentMode", "IW"),
                ee.Filter.equals("instrumentMode", "SM")
            ),
            ee.Filter.bounds(aoi),
            ee.Filter.eq('resolution_meters', 10),
            ee.Filter.date(date, date.advance(span+1, 'day'))
        ]

        s1_collection = ee.ImageCollection('COPERNICUS/S1_GRD').filter(filters)
        return s1_collection
    
    def createS1Composite(s1_collection):
        composite = ee.Image.cat([
            s1_collection.select('VH').mean(),
            s1_collection.select('VV').mean(),
            s1_collection.select('VH').mean()
        ])
        return composite.clip(aoi)

    def maskS2clouds(image):
        qa = image.select('QA60')

        # Bits 10 and 11 are clouds and cirrus, respectively.
        cloudBitMask = 1 << 10
        cirrusBitMask = 1 << 11

        # Both flags should be set to zero, indicating clear conditions.
        mask = qa.bitwiseAnd(cloudBitMask).eq(0).And(qa.bitwiseAnd(cirrusBitMask).eq(0))

        return image.updateMask(mask)
    
    def getSentinel2WithinDateRange(date, span):
        sentinel2 = ee.ImageCollection('COPERNICUS/S2') \
            .filterBounds(aoi) \
            .filterDate(date, date.advance(span+1, 'day')) \
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 70)) \
            .map(maskS2clouds) \
            .select('B4', 'B3', 'B2')
                        
        return sentinel2.mean().clip(aoi)
    
    def getS1Image(index):
        s1_collection = getSentinel1WithinDateRange(start_date[index], advance_days[index])
        return createS1Composite(s1_collection)

    def getS2Image(index):
        return getSentinel2WithinDateRange(start_date[index], advance_days[index])

    def calc_zscore(s1_collection_t1, s1_image_t2):
        anom = s1_image_t2 \
            .mean() \
            .subtract(s1_collection_t1.mean()) \
            .set({'system:time_start': s1_image_t2.get('system:time_start')})
        
        basesd = s1_collection_t1 \
            .reduce(ee.Reducer.stdDev()) \
            .rename(['VV', 'VH', 'angle'])
        
        return anom.divide(basesd) \
            .set({'system:time_start': anom.get('system:time_start')})

    def mapFloods(z, zvv_thd=-3, zvh_thd=-3, pow_thd=75, elev_thd=800, slp_thd=15):
        # JRC water mask
        jrc = ee.ImageCollection("JRC/GSW1_1/MonthlyHistory").filterDate('2016-01-01', '2019-01-01')
        jrcvalid = jrc.map(lambda x: x.gt(0)).sum()
        jrcwat = jrc.map(lambda x: x.eq(2)).sum().divide(jrcvalid).multiply(100)
        jrcmask = jrcvalid.gt(0)
        ow = jrcwat.gte(ee.Image(pow_thd))

        # add elevation and slope masking
        elevation = ee.Image('USGS/SRTMGL1_003').select('elevation')
        slope = ee.Terrain.slope(elevation)

        # Classify floods
        vvflag = z.select('VV').lte(ee.Image(zvv_thd))
        vhflag = z.select('VH').lte(ee.Image(zvh_thd))

        flood_class = ee.Image(0) \
            .add(vvflag) \
            .add(vhflag.multiply(2)) \
            .where(ow.eq(1), 4) \
            .rename('flood_class') \
            .where(elevation.gt(elev_thd).And(ow.neq(1)), 0) \
            .where(slope.gt(slp_thd).And(ow.neq(1)), 0)

        return flood_class

    flood = getFloodImage(getSentinel1WithinDateRange(start_date[0], advance_days[0]), 
                          getSentinel1WithinDateRange(start_date[1], advance_days[1]))
    flood_vector = getFloodShpUrl(flood, 3, 3, aoi, 10)
    vector_url = flood_vector.getDownloadURL(
        filetype = 'kml',
        filename = "flood"
        )
    return vector_url

def get_data(creds, expression):
    url = 'https://content-earthengine.googleapis.com/v1alpha/projects/earthengine-legacy/value:compute'

    headers = {
        'authority': 'content-earthengine.googleapis.com',
        'authorization': f'Bearer {creds["authToken"]}',
        'content-type': 'application/json',
        'origin': 'https://ryali93.users.earthengine.app',   
        'referer': 'https://ryali93.users.earthengine.app/', 
    }

    data = json.dumps({"expression": json.loads(expression)})
    response = requests.post(url, headers=headers, data=data)
    d = json.loads(response.text)
    return json.loads(d["result"])

def data_to_json(data):
    df = pd.DataFrame(data[1:], columns=data[0])
    df['time'] = pd.to_datetime(df['time'], unit='ms').dt.strftime('%Y-%m-%d')
    df = df[['time', 'NDVI', 'NDMI']]
    data_json = df.to_json(orient='records')
    return json.loads(data_json)

