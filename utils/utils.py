""" A few utility functions. At the moment simply functions used in recombinator which do not
require member variables. """

import sys
import os
import math
import glob
import collections
import csv
from opener import opener

from Bio import SeqIO

#----------------------------------------------------------------------------------------
eps = 1.e-10  # if things that should be 1.0 are this close to 1.0, blithely keep on keepin on. kinda arbitrary, but works for the moment. TODO actually replace the 1e-8s and 1e-10s with this constant
def is_normed(prob):
    return math.fabs(prob - 1.0) < eps  #*1000000000

# ----------------------------------------------------------------------------------------
regions = ['v', 'd', 'j']
erosions = ['v_3p', 'd_5p', 'd_3p', 'j_5p']
boundaries = ('vd', 'dj')
humans = ('A', 'B', 'C')
nukes = ('A', 'C', 'G', 'T')
maturities = ['memory', 'naive']  # NOTE eveywhere else I call this 'naivety' and give it the values 'M' or 'N'
naivities = ['M', 'N']
# Infrastrucure to allow hashing all the columns together into a dict key.
# Uses a tuple with the variables that are used to index selection frequencies
index_columns = ('v_gene', 'd_gene', 'j_gene', 'cdr3_length', 'v_3p_del', 'd_5p_del', 'd_3p_del', 'j_5p_del', 'vd_insertion', 'dj_insertion')
index_keys = {}
for i in range(len(index_columns)):  # dict so we can access them by name instead of by index number
    index_keys[index_columns[i]] = i

# ----------------------------------------------------------------------------------------
# Info specifying which parameters are assumed to correlate with which others. Taken from mutual
# information plot in bcellap repo

# key is parameter of interest, and associated list gives the parameters (other than itself) which are necessary to predict it
column_dependencies = {}
column_dependencies['v_gene'] = [] # TODO v choice actually depends on everything... but not super strongly, so a.t.m. I ignore it
column_dependencies['v_3p_del'] = ['v_gene']
column_dependencies['d_gene'] = []  # ['d_5p_del', 'd_3p_del'] TODO stop ignoring this correlation. Well, maybe. See note in hmmwriter.py
column_dependencies['d_5p_del'] = ['d_3p_del', 'd_gene']  # NOTE at least for now there's no way to specify the d erosion correlations
column_dependencies['d_3p_del'] = ['d_5p_del', 'd_gene']  #   in the hmm, so they're integrated out
column_dependencies['j_gene'] = []  # ['dj_insertion']  TODO see note above
column_dependencies['j_5p_del'] = [] # strange but seemingly true: does not depend on j choice. NOTE this makes normalization kinda fun when you read these out
column_dependencies['vd_insertion'] = []
column_dependencies['dj_insertion'] = ['j_gene']

# tuples with the column and its dependencies mashed together
# (first entry is the column of interest, and it depends upon the following entries)
column_dependency_tuples = []
for column, deps in column_dependencies.iteritems():
    tmp_list = [column]
    tmp_list.extend(deps)
    column_dependency_tuples.append(tuple(tmp_list))

def get_parameter_fname(column=None, deps=None, column_and_deps=None):
    """ return the file name in which we store the information for <column>. Either pass in <column> and <deps> *or* <column_and_deps> """
    if column == 'all':
        return 'all-probs.csv'
    if column_and_deps == None:
        column_and_deps = [column]
        column_and_deps.extend(deps)
    outfname = 'probs.csv'
    for ic in column_and_deps:
        outfname = ic + '-' + outfname
    return outfname

#----------------------------------------------------------------------------------------
def int_to_nucleotide(number):
    """ Convert between (0,1,2,3) and (A,C,G,T) """
    if number == 0:
        return 'A'
    elif number == 1:
        return 'C'
    elif number == 2:
        return 'G'
    elif number == 3:
        return 'T'
    else:
        print 'ERROR nucleotide number not in [0,3]'
        sys.exit()

# ----------------------------------------------------------------------------------------                    
def check_conserved_cysteine(seq, cyst_position, debug=False):
    """ Ensure there's a cysteine at <cyst_position> in <seq>. """
    if len(seq) < cyst_position+3:
        if debug:
            print 'ERROR seq not long enough in cysteine checker %d %s' % (cyst_position, seq)
        assert False
    cyst_word = str(seq[cyst_position:cyst_position+3])
    if cyst_word != 'TGT' and cyst_word != 'TGC':
        if debug:
            print 'ERROR cysteine in V is messed up: %s' % cyst_word
        assert False

# ----------------------------------------------------------------------------------------
def check_conserved_tryptophan(seq, tryp_position, debug=False):
    """ Ensure there's a tryptophan at <tryp_position> in <seq>. """
    if len(seq) < tryp_position+3:
        if debug:
            print 'ERROR seq not long enough in tryp checker %d %s' % (tryp_position, seq)
        assert False
    tryp_word = str(seq[tryp_position:tryp_position+3])
    if tryp_word != 'TGG':
        if debug:
            print 'ERROR tryptophan in J is messed up: %s' % tryp_word
        assert False

# ----------------------------------------------------------------------------------------
def check_conserved_codons(seq, cyst_position, tryp_position, debug=False):
    """ Double check that we conserved the cysteine and the tryptophan. """
    check_conserved_cysteine(seq, cyst_position, debug)
    check_conserved_tryptophan(seq, tryp_position, debug)

# ----------------------------------------------------------------------------------------
def are_conserved_codons_screwed_up(reco_event):
    """ Version that checks all the final seqs in reco_event.

    Returns True if codons are screwed up, or if no sequences have been added.
    """
    if len(reco_event.final_seqs) == 0:
        return True
    for seq in reco_event.final_seqs:
        try:
            check_conserved_codons(seq, reco_event.cyst_position, reco_event.final_tryp_position)
        except:
            return True

    return False

#----------------------------------------------------------------------------------------
def is_position_protected(protected_positions, prospective_position):
    """ Would a mutation at <prospective_position> screw up a protected codon? """
    for position in protected_positions:
        if (prospective_position == position or
            prospective_position == (position + 1) or
            prospective_position == (position + 2)):
            return True
    return False

#----------------------------------------------------------------------------------------
def would_erode_conserved_codon(reco_event):
    """ Would any of the erosion <lengths> delete a conserved codon? """
    lengths = reco_event.erosions
    # check conserved cysteine
    if len(reco_event.seqs['v']) - lengths['v_3p'] <= reco_event.cyst_position + 2:
        print '      about to erode cysteine (%d), try again' % lengths['v_3p']
        return True  # i.e. it *would* screw it up
    # check conserved tryptophan
    if lengths['j_5p'] - 1 >= reco_event.tryp_position:
        print '      about to erode tryptophan (%d), try again' % lengths['j_5p']
        return True

    return False  # *whew*, it won't erode either of 'em

#----------------------------------------------------------------------------------------
def is_erosion_longer_than_seq(reco_event):
    """ Are any of the proposed erosion <lengths> longer than the seq to be eroded? """
    lengths = reco_event.erosions
    if lengths['v_3p'] > len(reco_event.seqs['v']):  # NOTE not actually possible since we already know we didn't erode the cysteine
        print '      v_3p erosion too long (%d)' % lengths['v_3p']
        return True
    if lengths['d_5p'] + lengths['d_3p'] > len(reco_event.seqs['d']):
        print '      d erosions too long (%d)' % (lengths['d_5p'] + lengths['d_3p'])
        return True
    if lengths['j_5p'] > len(reco_event.seqs['j']):  # NOTE also not possible for the same reason
        print '      j_5p erosion too long (%d)' % lengths['j_5p']
        return True
    return False

#----------------------------------------------------------------------------------------
def find_tryp_in_joined_seq(gl_tryp_position_in_j, v_seq, vd_insertion, d_seq, dj_insertion, j_seq, j_erosion, debug=False):
    """ Find the <end> tryptophan in a joined sequence.

    Given local tryptophan position in the j region, figure
    out what position it's at in the final sequence.
    NOTE gl_tryp_position_in_j is the position *before* the j was eroded,
    but this fcn assumes that the j *has* been eroded.
    also NOTE <[vdj]_seq> are assumed to already be eroded
    """
    if debug:
        print 'checking tryp with: %s, %d - %d = %d' % (j_seq, gl_tryp_position_in_j, j_erosion, gl_tryp_position_in_j - j_erosion)
    check_conserved_tryptophan(j_seq, gl_tryp_position_in_j - j_erosion)  # make sure tryp is where it's supposed to be
    length_to_left_of_j = len(v_seq + vd_insertion + d_seq + dj_insertion)
    if debug:
        print '  finding tryp position as'
        print '    length_to_left_of_j = len(v_seq + vd_insertion + d_seq + dj_insertion) = %d + %d + %d + %d' % (len(v_seq), len(vd_insertion), len(d_seq), len(dj_insertion))
        print '    result = gl_tryp_position_in_j - j_erosion + length_to_left_of_j = %d - %d + %d = %d' % (gl_tryp_position_in_j, j_erosion, length_to_left_of_j, gl_tryp_position_in_j - j_erosion + length_to_left_of_j)
    return gl_tryp_position_in_j - j_erosion + length_to_left_of_j

# ----------------------------------------------------------------------------------------
Colors = {}
Colors['head'] = '\033[95m'
Colors['bold'] = '\033[1m'
Colors['purple'] = '\033[95m'
Colors['blue'] = '\033[94m'
Colors['green'] = '\033[92m'
Colors['yellow'] = '\033[93m'
Colors['red'] = '\033[91m'
Colors['end'] = '\033[0m'

def color(col, seq):
    assert col in Colors
    return Colors[col] + seq + Colors['end']

# ----------------------------------------------------------------------------------------
def color_mutants(ref_seq, seq, print_result=False):
    # assert len(ref_seq) == len(seq)
    return_str = ''
    for inuke in range(len(seq)):
        if inuke >= len(ref_seq) or seq[inuke] == ref_seq[inuke]:
            return_str += seq[inuke]
        else:
            return_str += color('red', seq[inuke])
    if print_result:
        print '%75s %s' % ('', ref_seq)
        print '%75s %s' % ('', return_str)
    return return_str

# ----------------------------------------------------------------------------------------
def color_gene(gene):
    return_str = gene[:3] + color('bold', color('red', gene[3])) + ' '  # add a space after
    n_version = gene[4 : gene.find('-')]
    n_subversion = gene[gene.find('-')+1 : gene.find('*')]
    if get_region(gene) == 'j':
        n_version = gene[4 : gene.find('*')]
        n_subversion = ''
        return_str += color('purple', n_version)
    else:
        return_str += color('purple', n_version) + '-' + color('purple', n_subversion)

    allele_end = gene.find('_')
    if allele_end < 0:
        allele_end = len(gene)
    allele = gene[gene.find('*')+1 : allele_end]
    return_str += '*' + color('yellow', allele)
    if '_' in gene:  # _F or _P in j gene names
        return_str += gene[gene.find('_') :]

    # hm, how about without all the crap in it?
    return_str = return_str.replace('IGH','  ').lower()
    return_str = return_str.replace('*',' ')
    return return_str

# ----------------------------------------------------------------------------------------
def is_mutated(original, final, n_muted=-1, n_total=-1):
    n_total += 1
    return_str = final
    if original != final:
        return_str = color('red', final)
        n_muted += 1
    return return_str, n_muted, n_total

# ----------------------------------------------------------------------------------------
def get_v_5p_del(original_seqs, line):
    original_length = len(original_seqs['v']) + len(original_seqs['d']) + len(original_seqs['j'])
    total_deletion_length = int(line['v_3p_del']) + int(line['d_5p_del']) + int(line['d_3p_del']) + int(line['j_5p_del'])
    total_insertion_length = len(line['vd_insertion']) + len(line['dj_insertion'])
    return original_length - total_deletion_length + total_insertion_length - len(line['seq'])

# ----------------------------------------------------------------------------------------
def get_reco_event_seqs(germlines, line, original_seqs, lengths, eroded_seqs):
    """
    get original and eroded germline seqs
    damn these function names kinda suck. TODO rejigger the function and variable names hereabouts
    """
    
    v_3p_del = int(line['v_3p_del'])
    d_5p_del = int(line['d_5p_del'])
    d_3p_del = int(line['d_3p_del'])
    j_5p_del = int(line['j_5p_del'])

    for region in regions:
        original_seqs[region] = germlines[region][line[region+'_gene']]
    if 'v_5p_del' not in line:  # try to infer the left-hand v 'deletion'
        line['v_5p_del'] = get_v_5p_del(original_seqs, line)
    original_seqs['v'] = original_seqs['v'][line['v_5p_del']:]  # TODO erm, should the 5p v erosion be off the original, or eroded sequence?

    # length (in the query sequence) which is assigned to each region
    lengths['v'] = len(original_seqs['v']) - v_3p_del
    lengths['d'] = len(original_seqs['d']) - d_5p_del - d_3p_del
    lengths['j'] = len(original_seqs['j']) - j_5p_del

    # the eroded germline sequences
    eroded_seqs['v'] = original_seqs['v'][:lengths['v']]
    eroded_seqs['d'] = original_seqs['d'][d_5p_del : len(original_seqs['d']) - d_3p_del]
    eroded_seqs['j'] = original_seqs['j'][j_5p_del :]

# ----------------------------------------------------------------------------------------
def add_cdr3_length(cyst_positions, tryp_positions, line, eroded_seqs):
    """ Add the cdr3_length to <line> based on the information already in <line> """
    eroded_gl_cpos = cyst_positions[line['v_gene']]['cysteine-position'] - int(line['v_5p_del'])  # cysteine position in eroded germline sequence
    eroded_gl_tpos = int(tryp_positions[line['j_gene']]) - int(line['j_5p_del'])
    try:
        check_conserved_cysteine(eroded_seqs['v'], eroded_gl_cpos, debug=True)
        check_conserved_tryptophan(eroded_seqs['j'], eroded_gl_tpos, debug=True)
        tpos_in_joined_seq = eroded_gl_tpos + len(eroded_seqs['v']) + len(line['vd_insertion']) + len(eroded_seqs['d']) + len(line['dj_insertion'])  # TODO dammit didn't I already do this somewhere up there?
        line['cdr3_length'] = tpos_in_joined_seq - eroded_gl_cpos + 3  # codon_positions['j'] - codon_positions['v'] + 3  #tryp_position_in_joined_seq - self.cyst_position + 3
    except AssertionError:
        print '    bad codon, setting cdr3_length to -1'
        line['cdr3_length'] = -1
    
# ----------------------------------------------------------------------------------------
def get_match_seqs(germlines, line, cyst_positions, tryp_positions):
    """
    get query match seqs (sections of the query sequence that are matched to germline) and their corresponding germline matches.
    NOTE adds them into <line>

    """

    original_seqs = {}  # original (non-eroded) germline seqs
    lengths = {}  # length of each match (including erosion)
    eroded_seqs = {}  # eroded germline seqs
    get_reco_event_seqs(germlines, line, original_seqs, lengths, eroded_seqs)
    add_cdr3_length(cyst_positions, tryp_positions, line, eroded_seqs)

    # add the <eroded_seqs> to <line> so we can find them later
    for region in regions:
        line[region + '_gl_seq'] = eroded_seqs[region]

    # the sections of the query sequence which are assigned to each region
    line['v_qr_seq'] = line['seq'][:len(eroded_seqs['v'])]  # NOTE I can't seem to escape the feeling that I've already done all this algebra somewhere else. *sigh*
    line['d_qr_seq'] = line['seq'][len(eroded_seqs['v']) + len(line['vd_insertion']) : len(eroded_seqs['v']) + len(line['vd_insertion']) + len(eroded_seqs['d'])]
    line['j_qr_seq'] = line['seq'][len(eroded_seqs['v']) + len(line['vd_insertion']) + len(eroded_seqs['d']) + len(line['dj_insertion']) : len(eroded_seqs['v']) + len(line['vd_insertion']) + len(eroded_seqs['d']) + len(line['dj_insertion']) + len(eroded_seqs['j'])]

# ----------------------------------------------------------------------------------------
def print_reco_event(germlines, line, cyst_position, final_tryp_position, one_line=False, extra_str=''):
    """ Print ascii summary of recombination event and mutation.

    If <one_line>, then only print out the final_seq line.
    """
    v_3p_del = int(line['v_3p_del'])  # TODO hurg don't really want these any more
    d_5p_del = int(line['d_5p_del'])
    d_3p_del = int(line['d_3p_del'])
    j_5p_del = int(line['j_5p_del'])

    original_seqs = {}  # original (non-eroded) germline seqs
    lengths = {}  # length of each match (including erosion)
    eroded_seqs = {}  # eroded germline seqs
    get_reco_event_seqs(germlines, line, original_seqs, lengths, eroded_seqs)

    germline_v_end = len(original_seqs['v']) - 1
    germline_d_start = lengths['v'] + len(line['vd_insertion']) - d_5p_del
    germline_d_end = germline_d_start + len(original_seqs['d'])
    germline_j_start = germline_d_end + 1 - d_3p_del + len(line['dj_insertion']) - j_5p_del

    if 'j_3p_del' in line:
        for _ in range(j_3p_del):
            line['seq'] += '.'

    final_seq = ''
    n_muted, n_total = 0,0
    for inuke in range(len(line['seq'])):
        ilocal = inuke
        new_nuke = ''
        if ilocal < lengths['v']:
            new_nuke, n_muted, n_total = is_mutated(eroded_seqs['v'][ilocal], line['seq'][inuke], n_muted, n_total)
        else:
            ilocal -= lengths['v']
            if ilocal < len(line['vd_insertion']):
                new_nuke, n_muted, n_total = is_mutated(line['vd_insertion'][ilocal], line['seq'][inuke], n_muted, n_total)
            else:
                ilocal -= len(line['vd_insertion'])
                if ilocal < lengths['d']:
                    new_nuke, n_muted, n_total = is_mutated(eroded_seqs['d'][ilocal], line['seq'][inuke], n_muted, n_total)
                else:
                    ilocal -= lengths['d']
                    if ilocal < len(line['dj_insertion']):
                        new_nuke, n_muted, n_total = is_mutated(line['dj_insertion'][ilocal], line['seq'][inuke], n_muted, n_total)
                    else:
                        ilocal -= len(line['dj_insertion'])
                        new_nuke, n_muted, n_total = is_mutated(eroded_seqs['j'][ilocal], line['seq'][inuke], n_muted, n_total)

        for pos in (cyst_position, final_tryp_position):  # reverse video for the conserved codon positions
            if pos > 0:
                adjusted_pos = pos - line['v_5p_del']  # adjust positions to allow for reads not extending all the way to left side of v
                if inuke == adjusted_pos:
                    new_nuke = '\033[7m' + new_nuke
                elif inuke == adjusted_pos + 2:
                    new_nuke = new_nuke + '\033[m'
        final_seq += new_nuke

    # pad with dots
    eroded_seqs['v'] = eroded_seqs['v'] + v_3p_del * '.'
    eroded_seqs['d'] = d_5p_del * '.' + eroded_seqs['d'] + d_3p_del * '.'
    eroded_seqs['j'] = j_5p_del * '.' + eroded_seqs['j']

    insertions = lengths['v'] * ' ' + line['vd_insertion'] + lengths['d'] * ' ' + line['dj_insertion'] + lengths['j'] * ' '
    d = germline_d_start * ' ' + eroded_seqs['d'] + (len(original_seqs['j']) - j_5p_del + len(line['dj_insertion']) - d_3p_del) * ' '
    vj = eroded_seqs['v'] + (germline_j_start - germline_v_end - 2) * ' ' + eroded_seqs['j']

    if 'score' not in line:
        line['score'] = ''
    if not one_line:
        print '%s    %s   inserts' % (extra_str, insertions)
        print '%s    %s   %s' % (extra_str, d, color_gene(line['d_gene']))
        print '%s    %s   %s,%s' % (extra_str, vj, color_gene(line['v_gene']), color_gene(line['j_gene']))
    print '%s    %s   %-10s muted: %5.2f' % (extra_str, final_seq, line['score'], float(n_muted) / n_total)

    line['seq'] = line['seq'].lstrip('.')  # hackey hackey hackey TODO change it
#    assert len(line['seq']) == line['v_5p_del'] + len(hmms['v']) + len(outline['vd_insertion']) + len(hmms['d']) + len(outline['dj_insertion']) + len(hmms['j']) + outline['j_3p_del']

#----------------------------------------------------------------------------------------
def sanitize_name(name):
    """ Replace characters in gene names that make crappy filenames. """
    saniname = name.replace('*', '_star_')
    saniname = saniname.replace('/', '_slash_')
    return saniname

#----------------------------------------------------------------------------------------
def unsanitize_name(name):
    """ Re-replace characters in gene names that make crappy filenames. """
    unsaniname = name.replace('_star_', '*')
    unsaniname = unsaniname.replace('_slash_', '/')
    return unsaniname

#----------------------------------------------------------------------------------------
def read_germlines(data_dir, remove_fp=False):
    """ <remove_fp> sometimes j names have a redundant _F or _P appended to their name. Set to True to remove this """
    germlines = {}
    for region in regions:
        germlines[region] = collections.OrderedDict()
        for seq_record in SeqIO.parse(data_dir + '/igh'+region+'.fasta', "fasta"):
            gene_name = seq_record.name
            if remove_fp and region == 'j':
                gene_name = gene_name[:-2]
            germlines[region][gene_name] = str(seq_record.seq)
    return germlines

# ----------------------------------------------------------------------------------------
def get_region(gene_name):
    """ return v, d, or j of gene"""
    assert 'IGH' in gene_name
    region = gene_name[3:4].lower()
    assert region in regions
    return region

# ----------------------------------------------------------------------------------------
def maturity_to_naivety(maturity):
    if maturity == 'memory':
        return 'M'
    elif maturity == 'naive':
        return 'N'
    else:
        assert False

# ----------------------------------------------------------------------------------------
def are_alleles(gene1, gene2):
    """
    Return true if gene1 and gene2 are alleles of the same gene version.
    Assumes they're alleles if everything left of the asterisk is the same, and everything more than two to the right of the asterisk is the same.
    """
    left_str_1 = gene1[0 : gene1.find('*')]
    left_str_2 = gene2[0 : gene1.find('*')]
    right_str_1 = gene1[gene1.find('*')+3 :]
    right_str_2 = gene2[gene1.find('*')+3 :]
    return left_str_1 == left_str_2 and right_str_1 == right_str_2

# ----------------------------------------------------------------------------------------
def are_same_primary_version(gene1, gene2):
    """
    Return true if the bit up to the dash is the same.
    There's probably a real name for that bit.
    """
    str_1 = gene1[0 : gene1.find('-')]
    str_2 = gene2[0 : gene2.find('-')]
    return str_1 == str_2

# ----------------------------------------------------------------------------------------
def read_overall_gene_prob(indir, only_region='', only_gene=''):
    counts = {}
    for region in regions:
        if only_region != '' and region != only_region:
            continue
        counts[region] = {}
        total = 0
        smallest_count = -1  # if we don't find the gene we're looking for, assume it occurs at the lowest rate at which we see any gene
        with opener('r')(indir + '/' + region + '_gene-probs.csv') as infile:  # TODO note this ignores correlations... which I think is actually ok, but it wouldn't hurt to think through it again at some point
            reader = csv.DictReader(infile)
            for line in reader:
                line_count = int(line['count'])
                gene = line[region + '_gene']
                total += line_count
                if line_count < smallest_count or smallest_count == -1:
                    smallest_count = line_count
                if gene not in counts[region]:
                    counts[region][gene] = 0
                counts[region][gene] += line_count
        if only_gene != '' and only_gene not in counts[region]:  # didn't find this gene
            counts[region][only_gene] = smallest_count
        # if region == 'v':
        #     for gene in ['IGHV3-30*12', 'IGHV3-30*07', 'IGHV3-30*03', 'IGHV3-30*10', 'IGHV3-30*11', 'IGHV3-30*06', 'IGHV3-30*19', 'IGHV3-30*17']:  # list of genes for which we don't have info
        #         print gene
        #         assert gene not in counts[get_region(gene)]
        #         counts[get_region(gene)][gene] = smallest_count
        for gene in counts[region]:
            counts[region][gene] /= float(total)
    # print 'return: %d / %d = %f' % (this_count, total, float(this_count) / total)
    if only_gene == '':
        return counts  # oops, now they're probs, not counts. *sigh*
    else:
        return counts[only_region][only_gene]

# ----------------------------------------------------------------------------------------
def hamming(seq1, seq2):
    assert len(seq1) == len(seq2)
    total = 0
    for ch1,ch2 in zip(seq1,seq2):
        if ch1 != ch2:
            total += 1
    return total

# ----------------------------------------------------------------------------------------
def get_key(query_name, second_query_name):
    """
    Return a hashable combination of the two query names that's the same if we reverse their order.
    At the moment, just add 'em up.
    """
    # assert query_name != ''
    # if second_query_name == '':
    #     second_query_name = '0'
    # return int(query_name) + int(second_query_name)
    assert query_name != ''
    if second_query_name == '':
        second_query_name = '0'
    return '.'.join(sorted([query_name, second_query_name]))

# ----------------------------------------------------------------------------------------
def prep_dir(dirname, wildling=None):
    """ make <dirname> if it d.n.e., and if shell glob <wildling> is specified, remove existing files which are thereby matched """
    if os.path.exists(dirname):
        if wildling != None:
            for fname in glob.glob(dirname + '/' + wildling):
                os.remove(fname)
    else:
        os.makedirs(dirname)
    assert len([fname for fname in os.listdir(dirname) if os.path.isfile(dirname + '/' + fname)]) == 0  # make sure there's no other files in the dir
