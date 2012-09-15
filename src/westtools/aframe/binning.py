from __future__ import division, print_function; __metaclass__ = type

import logging

log = logging.getLogger(__name__)

import numpy

import west
from westtools.aframe import AnalysisMixin

class BinningMixin(AnalysisMixin):
    '''A mixin for performing binning on WEST data.'''
    
    def __init__(self):
        super(BinningMixin,self).__init__()
        
        self.region_set = None
        self.n_bins = None
        self.n_dim = None
        
        self.discard_bin_assignments = False
        self.binning_h5gname = 'binning'
        self.binning_h5group = None
        self.region_set_hash = None

    def add_args(self, parser, upcall = True):
        if upcall:
            try:
                upfunc = super(BinningMixin,self).add_args
            except AttributeError:
                pass
            else:
                upfunc(parser)
        
        group = parser.add_argument_group('binning options')
        egroup = group.add_mutually_exclusive_group()
        egroup.add_argument('--binexpr', '--binbounds', dest='binexpr',
                            help='''Construct rectilinear bins from BINEXPR. This must be a list of lists of bin boundaries
                            (one list of bin boundaries for each dimension of the progress coordinate), formatted as a Python 
                            expression. E.g. "[[0,1,2,4,inf], [-inf,0,inf]]".''')
        group.add_argument('--discard-bin-assignments', dest='discard_bin_assignments', action='store_true',
                           help='''Discard any existing bin assignments stored in the analysis HDF5 file.''')
    
    def process_args(self, args, upcall = True):        
        if args.binexpr:
            west.rc.pstatus("Constructing rectilinear bin boundaries from the following expression: '{}'".format(args.binexpr))
            self.region_set = self.region_set_from_expr(args.binexpr)
        else:
            west.rc.pstatus('Loading bin boundaries from WEST system')
            system = west.rc.get_system_driver()
            self.region_set = system.new_region_set()
            
        self.n_bins = len(self.region_set.get_all_bins())
        self.n_dim = self.region_set.n_dim
        self.region_set_hash = self.region_set.identity_hash()
        west.rc.pstatus('  {:d} bins in {:d} dimension(s)'.format(self.n_bins, self.n_dim))
        west.rc.pstatus('  identity hash {}'.format(self.region_set_hash.hexdigest()))
        
        self.discard_bin_assignments = bool(args.discard_bin_assignments)
        
        if upcall:
            try:
                upfunc = super(BinningMixin,self).process_args
            except AttributeError:
                pass
            else:
                upfunc(args)
        
    def region_set_from_expr(self, expr):
        from west.pcoords import RectilinearRegionSet, PiecewiseRegionSet
        namespace = {'numpy': numpy,
                     'RectilinearRegionSet': RectilinearRegionSet,
                     'PiecewiseRegionSet': PiecewiseRegionSet,
                     'inf': float('inf')}
        
        try:
            return RectilinearRegionSet(eval(expr, namespace))
        except TypeError as e:
            if 'has no len' in str(e):
                raise ValueError('invalid bin boundary specification; you probably forgot to make a list of lists')

    def update_region_set(self,rs_type, args, kwargs):
        """ Update the region set to region set of type rs_type. All positional and keyword arguments for the specific region
            set type should be passed as a list (args) and dict (kwargs) of parameters respectively. """
        from west.pcoords import RectilinearRegionSet, PiecewiseRegionSet, VoronoiRegionSet

        rsets = {'RectilinearRegionSet': RectilinearRegionSet,
                 'PiecewiseRegionSet': PiecewiseRegionSet,
                 'VoronoiRegionSet': VoronoiRegionSet}
    
        if not rs_type in rsets:
            raise ValueError('invalid region set type {}; supported region set types: {}'.format(rs_type,rsets.keys()))
        else:
            west.rc.pstatus("Updating region set definition using {}".format(rs_type))
            
            self.region_set = rsets[rs_type](*args,**kwargs)
            self.n_bins = len(self.region_set.get_all_bins())
            self.n_dim = self.region_set.n_dim
            self.region_set_hash = self.region_set.identity_hash()
            west.rc.pstatus('  {:d} bins in {:d} dimension(s)'.format(self.n_bins, self.n_dim))
            west.rc.pstatus('  identity hash {}'.format(self.region_set_hash.hexdigest()))

    def write_bin_labels(self, dest, 
                         header='# bin labels:\n', 
                         format='# bin {bin_index:{max_iwidth}d} -- {label!s}\n'):
        '''Print labels for all bins in the given RegionSet (or ``self.region_set``) to ``dest``.  If provided, ``header`` 
        is printed before any labels.   The ``format`` string specifies how bin labels are to be printed.  Valid entries are:
          * ``bin_index`` -- the zero-based index of the bin
          * ``label`` -- the label, as obtained by ``bin.label``
          * ``max_iwidth`` -- the maximum width (in characters) of the bin index, for pretty alignment
        '''
        dest.write(header or '')
        bins = self.region_set.get_all_bins()
        max_iwidth = len(str(len(bins)-1))
        for (ibin, bin) in enumerate(bins):
            dest.write(format.format(bin_index=ibin, label=bin.label, max_iwidth=max_iwidth))
    
    def require_binning_group(self):
        if self.binning_h5group is None:
            self.binning_h5group = self.anal_h5file.require_group(self.binning_h5gname)
        return self.binning_h5group
    
    def delete_binning_group(self):
        self.binning_h5group = None
        del self.anal_h5file[self.binning_h5gname]

    def record_data_binhash(self, h5object):
        '''Record the identity hash for self.region_set as an attribute on the given HDF5 object (group or dataset)'''
        h5object.attrs['binhash'] = self.region_set_hash.digest()
        
    def check_data_binhash(self, h5object):
        '''Check whether the recorded bin identity hash on the given HDF5 object matches the identity hash for self.region_set'''
        return h5object.attrs.get('binhash') == self.region_set_hash.digest() 
            
    def assign_to_bins(self):
        '''Assign WEST segment data to bins.  Requires the DataReader mixin to be in the inheritance tree'''
        self.require_binning_group()        
        
        n_iters = self.last_iter - self.first_iter + 1
        max_n_segs = self.max_iter_segs_in_range(self.first_iter, self.last_iter)
        pcoord_len = self.get_pcoord_len(self.first_iter)
        
        assignments = numpy.zeros((n_iters, max_n_segs,pcoord_len), numpy.min_scalar_type(self.n_bins))
        populations = numpy.zeros((n_iters, pcoord_len, self.n_bins), numpy.float64)
        
        west.rc.pstatus('Assigning to bins...')
        
        for (iiter, n_iter) in enumerate(xrange(self.first_iter, self.last_iter+1)):
            west.rc.pstatus('\r  Iteration {:d}'.format(n_iter), end='')
            seg_index = self.get_seg_index(n_iter)
            pcoords = self.get_iter_group(n_iter)['pcoord'][...]
            weights = seg_index['weight']
            
            for seg_id in xrange(len(seg_index)):
                assignments[iiter,seg_id,:] = self.region_set.map_to_all_indices(pcoords[seg_id,:,:])
            
            for it in xrange(pcoord_len):
                populations[iiter, it, :] = numpy.bincount(assignments[iiter,:len(seg_index),it], weights, minlength=self.n_bins)
        
            west.rc.pflush()
            del pcoords, weights, seg_index
         
        assignments_ds = self.binning_h5group.create_dataset('bin_assignments', data=assignments, compression='gzip')
        populations_ds = self.binning_h5group.create_dataset('bin_populations', data=populations, compression='gzip')
        
        for h5object in (self.binning_h5group, assignments_ds, populations_ds):
            self.record_data_iter_range(h5object)
            self.record_data_iter_step(h5object, 1)
            self.record_data_binhash(h5object)
                
        west.rc.pstatus()
            
    def require_bin_assignments(self):
        self.require_binning_group()
        do_assign = False
        if self.discard_bin_assignments:
            west.rc.pstatus('Discarding existing bin assignments.')
            do_assign = True
        elif 'bin_assignments' not in self.binning_h5group:
            do_assign = True
        elif not self.check_data_iter_range_least(self.binning_h5group):
            west.rc.pstatus('Existing bin assignments are for incompatible first/last iterations; deleting assignments.')
            do_assign = True
        elif not self.check_data_binhash(self.binning_h5group):
            west.rc.pstatus('Bin definitions have changed; deleting existing bin assignments.')
            do_assign = True
    
        if do_assign:
            self.delete_binning_group()
            self.assign_to_bins()
        else:
            west.rc.pstatus('Using existing bin assignments.')
            
    def get_bin_assignments(self, first_iter = None, last_iter = None):
        return self.slice_per_iter_data(self.binning_h5group['bin_assignments'], first_iter, last_iter)

    def get_bin_populations(self, first_iter = None, last_iter = None):
        return self.slice_per_iter_data(self.binning_h5group['bin_populations'], first_iter, last_iter)
    
class BFBinningMixin(BinningMixin):
    '''Modifications of BinningMixin to do binning on brute force data (as stored in an HDF5 file
    created by BFDataManager).'''
    
    def assign_to_bins(self, chunksize=65536):
        '''Assign brute force trajectory data to bins.'''
        self.require_bf_h5file()
        self.require_binning_group()
        n_trajs = self.get_n_trajs()
        max_traj_len = self.get_max_traj_len()
        
        assignments_ds = self.binning_h5group.create_dataset('bin_assignments', 
                                                             shape=(n_trajs,max_traj_len),
                                                             dtype=numpy.min_scalar_type(self.n_bins),
                                                             chunks=(1,chunksize),
                                                            compression='gzip')                
        west.rc.pstatus('Assigning to bins...')
        
        for traj_id in xrange(n_trajs):
            pcoord_ds = self.get_pcoord_dataset(traj_id)
            pclen = pcoord_ds.len()
            for istart in xrange(0,pclen,chunksize):
                iend = min(istart+chunksize,pclen)
                pcchunk = pcoord_ds[istart:iend]
                assignments_ds[traj_id,istart:iend] = self.region_set.map_to_all_indices(pcchunk)
                west.rc.pstatus('\r  Trajectory {:d}:  {:d}/{:d}'.format(traj_id,iend,pclen), end='')
                west.rc.pflush()
                del pcchunk
            west.rc.pstatus()
            del pcoord_ds
        
        for h5object in (self.binning_h5group, assignments_ds):
            self.record_data_binhash(h5object)
                            
    def require_bin_assignments(self):
        self.require_binning_group()
        do_assign = False
        if self.discard_bin_assignments:
            west.rc.pstatus('Discarding existing bin assignments.')
            do_assign = True
        elif 'bin_assignments' not in self.binning_h5group:
            do_assign = True
        elif not self.check_data_binhash(self.binning_h5group):
            west.rc.pstatus('Bin definitions have changed; deleting existing bin assignments.')
            do_assign = True
        
        if do_assign:
            self.delete_binning_group()
            self.assign_to_bins()