"""Render the simple vector icon to Windows' multi-resolution ICO and PNG."""
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image,ImageDraw


def build_icon():
    assets=Path(__file__).resolve().parents[1]/'assets'
    scale=4;image=Image.new('RGBA',(256*scale,256*scale));draw=ImageDraw.Draw(image)
    for element in ET.parse(assets/'spectrum_studio.svg').getroot():
        tag=element.tag.rsplit('}',1)[-1];attrs=element.attrib
        number=lambda name:float(attrs.get(name,0))*scale
        if tag=='rect':
            x,y=number('x'),number('y');fill=attrs.get('fill')
            draw.rounded_rectangle((x,y,x+number('width'),y+number('height')),radius=number('rx'),
                fill=None if fill=='none' else fill,outline=attrs.get('stroke'),width=round(number('stroke-width')) or 1)
        elif tag=='polyline':
            points=[tuple(float(value)*scale for value in pair.split(',')) for pair in attrs['points'].split()]
            width=round(number('stroke-width'));color=attrs['stroke']
            draw.line(points,fill=color,width=width,joint='curve')
            for x,y in points:draw.ellipse((x-width/2,y-width/2,x+width/2,y+width/2),fill=color)
        elif tag not in {'title','desc'}:raise ValueError('Unsupported icon vector element: '+tag)
    image=image.resize((256,256),Image.Resampling.LANCZOS)
    image.save(assets/'spectrum_studio.png')
    image.save(assets/'spectrum_studio.ico',sizes=[(size,size) for size in [16,20,24,32,40,48,64,128,256]])
    return assets/'spectrum_studio.ico'


if __name__=='__main__':print(build_icon())
