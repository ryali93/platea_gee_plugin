from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand
from qgis.PyQt.QtCore import pyqtSignal
from qgis.core import QgsRectangle, QgsWkbTypes, QgsPointXY
from qgis.PyQt.QtGui import QColor

import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from datetime import datetime

class RectangleMapTool(QgsMapToolEmitPoint):
    """
    This class is used to select a rectangle on the map canvas.
    """
    rectangleSelected = pyqtSignal(QgsRectangle)

    def __init__(self, canvas):
        self.canvas = canvas
        QgsMapToolEmitPoint.__init__(self, self.canvas)
        self.rubberBand = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.rubberBand.setColor(QColor(255, 0, 0, 100))
        self.rubberBand.setWidth(1)
        self.reset()

    def reset(self):
        """Reset the rubber band and the rectangle"""
        self.startPoint = self.endPoint = None
        self.isEmittingPoint = False
        self.rubberBand.reset(QgsWkbTypes.PolygonGeometry)

    def canvasPressEvent(self, e):
        """Called when the mouse is pressed on the canvas."""
        self.startPoint = self.toMapCoordinates(e.pos())
        self.endPoint = self.startPoint
        self.isEmittingPoint = True
        self.showRect(self.startPoint, self.endPoint)

    def canvasReleaseEvent(self, e):
        """Called when the mouse is released."""
        self.isEmittingPoint = False
        r = self.rectangle()
        if r is not None:
            self.rectangleSelected.emit(r)
        self.reset()

    def canvasMoveEvent(self, e):
        """Called when the mouse is moved."""
        if not self.isEmittingPoint:
            return

        self.endPoint = self.toMapCoordinates(e.pos())
        self.showRect(self.startPoint, self.endPoint)

    def showRect(self, startPoint, endPoint):
        """Draws a rectangle between two points."""
        self.rubberBand.reset(QgsWkbTypes.PolygonGeometry)
        if startPoint.x() == endPoint.x() or startPoint.y() == endPoint.y():
            return

        point1 = QgsPointXY(startPoint.x(), startPoint.y())
        point2 = QgsPointXY(startPoint.x(), endPoint.y())
        point3 = QgsPointXY(endPoint.x(), endPoint.y())
        point4 = QgsPointXY(endPoint.x(), startPoint.y())

        self.rubberBand.addPoint(point1, False)
        self.rubberBand.addPoint(point2, False)
        self.rubberBand.addPoint(point3, False)
        self.rubberBand.addPoint(point4, True)
        self.rubberBand.show()

    def rectangle(self):
        """Returns the selected rectangle."""
        if self.startPoint is None or self.endPoint is None:
            return None
        elif self.startPoint.x() == self.endPoint.x() or self.startPoint.y() == self.endPoint.y():
            return None
        return QgsRectangle(self.startPoint, self.endPoint)

def create_plot_series(data):
    # Extract the lists of time, NDVI and NDMI
    time = [entry['time'] for entry in data]
    ndvi = [entry['NDVI'] for entry in data]
    ndmi = [entry['NDMI'] for entry in data]

    time = [datetime.strptime(d, '%Y-%m-%d') for d in time]
    
    ranges = [(-0.2, 0), (0, 0.2), (0.2, 0.5), (0.5, 1)]
    colors = ['#a50026', '#f46d43', '#ffffbf', '#1a9850']

    # Create the plot with Matplotlib
    plt.rc('font', family='serif')
    fig, ax = plt.subplots()  # Crear la figura y los ejes aquí
    ax.plot(time, ndvi, marker='o', label='NDVI')
    ax.plot(time, ndmi, marker='o', label='NDMI')
    ax.set_title('NDVI y NDMI')
    ax.set_ylim([-0.2, 1])
    for color, range in zip(colors, ranges):
        ax.fill_between(time, range[0], range[1], facecolor=color, alpha=0.3)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())  # Use AutoDateLocator for ticks
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))  # Set the formatter
    fig.autofmt_xdate()  # Set the x-axis to autoformat the dates
    ax.legend()
    ax.grid()
    return fig