import os
import numpy as np
from datetime import datetime

from opendrift import OpenDriftSimulation
from elements import LagrangianArray


# Defining the oil element properties
class Oil(LagrangianArray):
    """Extending LagrangianArray with variables relevant for oil particles."""

    variables = LagrangianArray.add_variables([
        ('mass_oil', {'dtype': np.float32,
                      'units': 'kg',
                      'default': 1}),
        ('viscosity', {'dtype': np.float32,
                       #'unit': 'mm2/s (centiStokes)',
                       'units': 'N s/m2 (Pa s)',
                       'default': 0.5}),
        ('density', {'dtype': np.float32,
                     'units': 'kg/m^3',
                     'default': 800}),
        ('age_seconds', {'dtype': np.float32,
                         'units': 's',
                         'default': 0}),
        ('age_exposure_seconds', {'dtype': np.float32,
                                  'units': 's',
                                  'default': 0}),
        ('age_emulsion_seconds', {'dtype': np.float32,
                                  'units': 's',
                                  'default': 0}),
        ('mass_emulsion', {'dtype': np.float32,
                           'units': 'kg',
                           'default': 0}),
        ('fraction_evaporated', {'dtype': np.float32,
                                 'units': '%',
                                 'default': 0}),
        ('water_content', {'dtype': np.float32,
                           'units': '%',
                           'default': 0})])


class OpenOil(OpenDriftSimulation):
    """Open source oil trajectory model based on the OpenDrift framework.

        Developed at MET Norway based on oil weathering parameterisations
        found in open/published litterature.

        Under construction.
    """

    ElementType = Oil

    required_variables = ['x_sea_water_velocity', 'y_sea_water_velocity',
                          'sea_surface_wave_significant_height',
                          'sea_surface_wave_to_direction',
                          'x_wind', 'y_wind', 'land_binary_mask']

    fallback_values = {'x_sea_water_velocity': 0,
                       'y_sea_water_velocity': 0,
                       'sea_surface_wave_significant_height': 0,
                       'sea_surface_wave_to_direction': 0,
                       'x_wind': 0, 'y_wind': 0}

    # Default colors for plotting
    status_colors = {'initial': 'green', 'active': 'blue',
                     'missing_data': 'gray', 'stranded': 'red',
                     'evaporated': 'yellow', 'dispersed': 'magenta'}

    # Read oil types from file (presently only for illustrative effect)
    oil_types = str([str(l.strip()) for l in open(
                    os.path.dirname(os.path.realpath(__file__))
                    + '/oil_types.txt').readlines()])[1:-1]
    default_oil = oil_types.split(',')[0].strip()

    # Configuration

    configspec = '''
        [input]
            readers = list(min=1, default=list(''))
            [[spill]]
                longitude = float(min=-360, max=360, default=5)
                latitude = float(min=-90, max=90, default=60)
                time = string(default='%s')
                oil_type = option(%s, default=%s)
        [processes]
            dispersion = boolean(default=True)
            diffusion = boolean(default=True)
            evaporation = boolean(default=True)
            emulsification = boolean(default=True)
        [drift]
            wind_drift_factor = float(min=0, max=1, default=0.02)
            current_uncertainty = float(min=0, max=5, default=.1)
            wind_uncertainty = float(min=0, max=5, default=1)
    ''' % (datetime.now().strftime('%Y-%d-%m %H:00'), oil_types, default_oil)


    def __init__(self, *args, **kwargs):

        # Read oil properties from file
        d = os.path.dirname(os.path.realpath(__file__))
        oilprop = np.loadtxt(d + '/prop.dat')
        self.model.fref = oilprop[1:, 1]*.01  # Evaporation as fraction
        self.model.tref = oilprop[1:, 0]*3600  # Time in seconds
        self.model.wmax = oilprop[1:, 3]  #
        self.model.reference_wind = oilprop[0, 1]
        self.model.reference_thickness = oilprop[0, 0]

        # Calling general constructor of parent class
        super(OpenOil, self).__init__(*args, **kwargs)

    def update(self):
        """Update positions and properties of oil particles."""

        self.elements.age_seconds += self.time_step.total_seconds()

        # Calculate windspeed, which is needed for emulsification,
        # and dispersion (if wave height is not given)
        windspeed = np.sqrt(self.environment.x_wind**2 +
                            self.environment.y_wind**2)

        ################
        ## Evaporation
        ################
        if self.config['processes']['evaporation'] is True:
            # Store evaporation fraction at beginning of timestep
            fraction_evaporated_previous = self.elements.fraction_evaporated

            # Evaporate only elements at surface
            at_surface = (self.elements.depth == 0)
            if np.isscalar(at_surface):
                at_surface = at_surface*np.ones(self.num_elements_active(),
                                                dtype=bool)
            Urel = windspeed/self.model.reference_wind  # Relative wind
            h = 2  # Film thickness in mm, harcoded for now

            # Calculate exposure time
            #   presently without compensation for sea temperature
            delta_exposure_seconds = \
                (self.model.reference_thickness/h)*Urel * \
                self.time_step.total_seconds()
            if np.isscalar(self.elements.age_exposure_seconds):
                self.elements.age_exposure_seconds += delta_exposure_seconds
            else:
                self.elements.age_exposure_seconds[at_surface] += \
                    delta_exposure_seconds[at_surface]

            self.elements.fraction_evaporated = np.interp(
                self.elements.age_exposure_seconds,
                self.model.tref, self.model.fref)

            # Evaporation probability equals the difference in fraction
            # evaporated at this timestep compared to previous timestep,
            # divided by the remaining fraction of the particle at 
            # previous timestep
            evaporation_probability = ((self.elements.fraction_evaporated -
                                        fraction_evaporated_previous) /
                                       (1 - fraction_evaporated_previous))
            evaporation_probability[~at_surface] = 0
            self.deactivate_elements(evaporation_probability >
                                     np.random.rand(self.num_elements_active(),),
                                     reason='evaporated')
        ##################
        # Emulsification
        ##################
        if self.config['processes']['emulsification'] is True:
            # Apparent emulsion age of particles
            self.elements.age_emulsion_seconds += \
                Urel*self.time_step.total_seconds()

            self.elements.water_content = np.interp(
                self.elements.age_emulsion_seconds,
                self.model.tref, self.model.wmax)

        ###############
        # Dispersion
        ###############
        if self.config['processes']['dispersion'] is True:

            # From NOAA PyGnome model:
            # https://github.com/NOAA-ORR-ERD/PyGnome/ 
            v_entrain = 3.9E-8
            sea_water_density = 1028
            fraction_breaking_waves = 0.02
            wave_significant_height = \
                self.environment.sea_surface_wave_significant_height
            wave_significant_height[wave_significant_height==0] = \
                0.0246*windspeed[wave_significant_height==0]**2
            dissipation_wave_energy = (0.0034*sea_water_density*9.81*
                                       wave_significant_height**2)
            c_disp = np.power(dissipation_wave_energy, 0.57) * \
                        fraction_breaking_waves
            # Roy's constant
            C_Roy = 2400.0 * np.exp(-73.682*np.sqrt(
                                self.elements.viscosity/self.elements.density))

            q_disp = C_Roy * c_disp * v_entrain / self.elements.density

            oil_mass_loss = (q_disp * self.time_step.total_seconds() *
                             self.elements.density)

            self.elements.mass_oil -= oil_mass_loss

            #self.elements.depth = \
            #    self.environment.sea_surface_wave_significant_height

            ## Marks R. (1987), Marine aerosols and whitecaps in the
            ## North Atlantic and Greenland sea regions 
            ## Deutsche Hydrografische Zeitschrift, Vol 40, Issue 2 , pp 71-79
            #whitecap_coverage = (2.54E-4)*np.power(windspeed, 3.58)  # percent

            ## Martinsen et al. (1994), The operational oil drift system at DNMI
            ## DNMI Technical report No 125, 51 p 
            #wave_period = 3.85*np.sqrt(
            #    self.environment.sea_surface_wave_significant_height)  # sec

            #time_between_breaking_events = 3.85/whitecap_coverage  # TBC

            #rho_w = 1025  # kg/m3
            #wave_period[wave_period == 0] = 5  # NB: temporal fix for no waves
            #dissipation_energy = 0.0034*rho_w*9.81*(wave_period**2)
            #dsize_coeff = 2100  # Wettre et al., Appendix A
            #c_oil = dsize_coeff*np.power(self.elements.viscosity, -0.4)
            ## Random numbers between 0 and 1:
            #p = np.random.rand(self.num_elements_active(), )
            #oil_per_unit_surface = 1
            #droplet_size = np.power((p*oil_per_unit_surface)/(c_oil *
            #                        np.power(dissipation_energy, 0.57)),
            #                        1/1.17)
            #self.deactivate_elements(droplet_size < 5E-7, reason='dispersed')

        # Deactivate elements on land
        self.deactivate_elements(self.environment.land_binary_mask == 1,
                                 reason='stranded')

        # Simply move particles with ambient current
        self.update_positions(self.environment.x_sea_water_velocity,
                              self.environment.y_sea_water_velocity)

        # Wind drag
        wind_drift_factor = self.config['drift']['wind_drift_factor']
        self.update_positions(self.environment.x_wind*wind_drift_factor,
                              self.environment.y_wind*wind_drift_factor)

        # Uncertainty / diffusion
        if self.config['processes']['diffusion'] is True:
            # Current
            std_current_comp = self.config['drift']['current_uncertainty']
            if std_current_comp > 0:
                sigma_u = np.random.normal(0, std_current_comp,
                                           self.num_elements_active())
                sigma_v = np.random.normal(0, std_current_comp,
                                           self.num_elements_active())
            else:
                sigma_u = 0*self.environment.x_wind
                sigma_v = 0*self.environment.x_wind
            self.update_positions(sigma_u, sigma_v)

            # Wind
            std_wind_comp = self.config['drift']['wind_uncertainty']
            if wind_drift_factor > 0 and std_wind_comp > 0:
                sigma_u = np.random.normal(0, std_wind_comp*wind_drift_factor,
                                           self.num_elements_active())
                sigma_v = np.random.normal(0, std_wind_comp*wind_drift_factor,
                                           self.num_elements_active())
            else:
                sigma_u = 0*self.environment.x_wind
                sigma_v = 0*self.environment.x_wind
            self.update_positions(sigma_u, sigma_v)

    def seed_from_gml(self, gmlfile, num_elements=1000):
        """Read oil slick contours from GML file, and seed particles within."""

        # Specific imports
        import datetime
        import matplotlib.nxutils as nx
        from xml.etree import ElementTree
        from matplotlib.patches import Polygon
        from mpl_toolkits.basemap import pyproj

        namespaces = {'od': 'http://cweb.ksat.no/cweb/schema/geoweb/oil',
                      'gml': 'http://www.opengis.net/gml'}
        slicks = []

        with open(gmlfile, 'rt') as e:
            tree = ElementTree.parse(e)

        pos1 = 'od:oilDetectionMember/od:oilDetection/od:oilSpill/gml:Polygon'
        pos2 = 'gml:exterior/gml:LinearRing/gml:posList'

        # This retrieves some other types of patches, found in some files only
        # Should be combines with the above, to get all patches
        #pos1 = 'od:oilDetectionMember/od:oilDetection/od:oilSpill/gml:Surface/gml:polygonPatches'
        #pos2 = 'gml:PolygonPatch/gml:exterior/gml:LinearRing/gml:posList'

        # Find detection time
        time_pos = 'od:oilDetectionMember/od:oilDetection/od:detectionTime'
        self.start_time = datetime.datetime.strptime(
            tree.find(time_pos, namespaces).text, '%Y-%m-%dT%H:%M:%S.%fZ')

        for patch in tree.findall(pos1, namespaces):
            pos = patch.find(pos2, namespaces).text
            c = np.array(pos.split()).astype(np.float)
            lon = c[0::2]
            lat = c[1::2]
            slicks.append(Polygon(zip(lon, lat)))

        # Find boundary and area of all patches
        lons = np.array([])
        lats = lons.copy()
        for slick in slicks:
            ext = slick.get_extents()
            lons = np.append(lons, [ext.xmin, ext.xmax])
            lats = np.append(lats, [ext.ymin, ext.ymax])
            # Make a stereographic projection centred on the polygon
        lonmin = lons.min()
        lonmax = lons.max()
        latmin = lats.min()
        latmax = lats.max()

        # Place n points within the polygons
        proj = pyproj.Proj('+proj=aea +lat_1=%f +lat_2=%f +lat_0=%f +lon_0=%f'
                           % (latmin, latmax,
                              (latmin+latmax)/2, (lonmin+lonmax)/2))
        slickarea = np.array([])
        for slick in slicks:
            lonlat = slick.get_xy()
            lon = lonlat[:, 0]
            lat = lonlat[:, 1]
            x, y = proj(lon, lat)

            area_of_polygon = 0.0
            for i in xrange(-1, len(x)-1):
                area_of_polygon += x[i] * (y[i+1] - y[i-1])
            area_of_polygon = abs(area_of_polygon) / 2.0
            slickarea = np.append(slickarea, area_of_polygon)  # in m2

        # Make points
        deltax = np.sqrt(np.sum(slickarea)/num_elements)

        lonpoints = np.array([])
        latpoints = np.array([])
        for i, slick in enumerate(slicks):
            lonlat = slick.get_xy()
            lon = lonlat[:, 0]
            lat = lonlat[:, 1]
            x, y = proj(lon, lat)
            xvec = np.arange(x.min(), x.max(), deltax)
            yvec = np.arange(y.min(), y.max(), deltax)
            x, y = np.meshgrid(xvec, yvec)
            lon, lat = proj(x, y, inverse=True)
            lon = lon.ravel()
            lat = lat.ravel()
            points = np.c_[lon, lat]
            ind = nx.points_inside_poly(points, slick.xy)
            lonpoints = np.append(lonpoints, lon[ind])
            latpoints = np.append(latpoints, lat[ind])

        # Finally seed at found positions
        kwargs = {}
        kwargs['lon'] = lonpoints
        kwargs['lat'] = latpoints
        kwargs['ID'] = np.arange(len(lonpoints)) + 1
        kwargs['mass_oil'] = 1
        self.elements = self.ElementType(**kwargs)
