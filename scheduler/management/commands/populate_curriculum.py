from django.core.management.base import BaseCommand
from scheduler.models import CurriculumLevel, CurriculumTopic, TopicPrerequisite


class Command(BaseCommand):
    help = 'Populate the database with comprehensive chess curriculum content'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing curriculum data before populating',
        )

    def handle(self, *args, **options):
        self.stdout.write('🎯 Populating Chess Training Curriculum...')
        
        # Clear existing data if requested
        if options['clear']:
            self.stdout.write('Clearing existing data...')
            TopicPrerequisite.objects.all().delete()
            CurriculumTopic.objects.all().delete()
            CurriculumLevel.objects.all().delete()
        
        # Create curriculum levels
        self.create_levels()
        
        # Create comprehensive curriculum topics
        self.create_foundation_topics()
        self.create_tactical_topics()
        self.create_strategic_topics()
        self.create_advanced_topics()
        self.create_mastery_topics()
        
        # Set up prerequisites
        self.create_prerequisites()
        
        self.stdout.write(
            self.style.SUCCESS('✅ Successfully populated chess curriculum!')
        )
        
        # Print summary
        levels_count = CurriculumLevel.objects.count()
        topics_count = CurriculumTopic.objects.count()
        prereqs_count = TopicPrerequisite.objects.count()
        
        self.stdout.write(f'📊 Summary: {levels_count} levels, {topics_count} topics, {prereqs_count} prerequisites')

    def create_levels(self):
        """Create the main curriculum levels"""
        levels_data = [
            {
                'name': 'FOUNDATION',
                'description': 'Basic chess rules, piece movement, and fundamental concepts. Students learn how to play the game correctly.',
                'min_elo': 400,
                'max_elo': 600,
                'sort_order': 1
            },
            {
                'name': 'TACTICAL',
                'description': 'Pattern recognition and basic tactical combinations. Students learn to find simple winning moves.',
                'min_elo': 600,
                'max_elo': 800,
                'sort_order': 2
            },
            {
                'name': 'STRATEGIC',
                'description': 'Positional understanding and planning. Students learn when to make different types of moves.',
                'min_elo': 800,
                'max_elo': 1000,
                'sort_order': 3
            },
            {
                'name': 'ADVANCED',
                'description': 'Complex patterns and deeper understanding. Students develop consistent play across all game phases.',
                'min_elo': 1000,
                'max_elo': 1200,
                'sort_order': 4
            },
            {
                'name': 'MASTERY',
                'description': 'Mastery-level concepts and competitive play. Students achieve tournament-level understanding.',
                'min_elo': 1200,
                'max_elo': 1600,
                'sort_order': 5
            }
        ]
        
        for level_data in levels_data:
            level, created = CurriculumLevel.objects.get_or_create(
                name=level_data['name'],
                defaults=level_data
            )
            if created:
                self.stdout.write(f'Created level: {level.get_name_display()}')

    def create_foundation_topics(self):
        """Foundation Level Topics (400-600 ELO)"""
        foundation = CurriculumLevel.objects.get(name='FOUNDATION')
        
        topics = [
            # Piece Basics Category
            {
                'name': 'How the Pawn Moves',
                'category': 'Piece Basics',
                'sort_order': 1,
                'learning_objective': 'Student can correctly move pawns and understands all pawn movement rules including first-move, capture, and promotion.',
                'teaching_method': '''1. Place a pawn on its starting square
2. Show forward one-square movement
3. Demonstrate first-move two-square option
4. Explain capture diagonally (use pieces to capture)
5. Show what happens at the end of the board (promotion)
6. Practice with "pawn races" across the board''',
                'practice_activities': '''• Pawn racing game (first to promote wins)
• "Capture the piece" - place pieces diagonally, have student capture them
• Promotion quiz - "What happens when pawn reaches the end?"
• Pawn vs pawn mini-games''',
                'pass_criteria': '''• Correctly moves pawn forward in 5/5 attempts
• Successfully demonstrates diagonal capture 3/3 times
• Can explain promotion concept and choose promotion piece
• Shows understanding of pawn's limitations (can't move backward)''',
                'enhancement_activities': '''• En passant introduction (basic concept only)
• Pawn structure basics - doubled pawns, isolated pawns
• Simple pawn endgames - opposition concept
• "Pawn and King vs King" endgame basics''',
                'common_mistakes': '''• Moving pawn diagonally without capturing
• Moving pawn backward
• Forgetting about promotion
• Two-square move when not on starting square''',
                'estimated_time_min': 10,
                'estimated_time_max': 20,
                'elo_points': 15
            },
            {
                'name': 'How the Rook Moves',
                'category': 'Piece Basics',
                'sort_order': 2,
                'learning_objective': 'Student can move the rook correctly along ranks and files, understands captures and blocking.',
                'teaching_method': '''1. Place rook in center of board (e4)
2. Show horizontal movement (along ranks)
3. Show vertical movement (along files) 
4. Demonstrate captures replace the piece
5. Show how pieces block the rook's path
6. Practice "rook hunt" - capture specific pieces''',
                'practice_activities': '''• "Clear the board" - rook must capture all pawns
• Rook maze - navigate around blocked squares
• "Rook vs pawns" - defend or attack with rook
• Coordinate practice - "Move rook to d7"''',
                'pass_criteria': '''• Identifies 5/5 legal rook moves from given position
• Explains why 3/3 illegal moves don't work (blocked path)
• Demonstrates capture without prompting
• Shows rook can't move diagonally''',
                'enhancement_activities': '''• Rook endgames - back rank mate patterns
• Rook and king coordination
• "Cutting off the king" concepts
• Basic rook vs pawn endgames''',
                'common_mistakes': '''• Moving diagonally like a bishop
• Jumping over pieces
• Not understanding captures replace pieces
• Confusing with queen movement''',
                'estimated_time_min': 10,
                'estimated_time_max': 25,
                'elo_points': 15
            },
            {
                'name': 'How the Bishop Moves',
                'category': 'Piece Basics',
                'sort_order': 3,
                'learning_objective': 'Student understands diagonal movement, light/dark square concepts, and bishop limitations.',
                'teaching_method': '''1. Place bishop on light square (f1)
2. Show diagonal movement in all directions
3. Explain light-square vs dark-square concept
4. Demonstrate captures and blocking
5. Show bishop can't reach squares of opposite color
6. Practice "connect the diagonals"''',
                'practice_activities': '''• Color the squares game - identify bishop's possible moves
• "Bishop maze" - navigate around blocked diagonals
• Light vs dark square awareness exercises
• "Opposite bishops" mini-games''',
                'pass_criteria': '''• Moves bishop correctly on diagonals 5/5 times
• Identifies light-square vs dark-square bishop
• Explains why bishop can't reach certain squares
• Demonstrates capture on diagonal''',
                'enhancement_activities': '''• "Good bishop vs bad bishop" concepts
• Bishop pair advantages
• Fianchetto development patterns
• Basic bishop endgames''',
                'common_mistakes': '''• Moving along ranks or files
• Trying to move to opposite-colored squares
• Jumping over pieces
• Confusing with queen movement''',
                'estimated_time_min': 12,
                'estimated_time_max': 25,
                'elo_points': 15
            },
            {
                'name': 'How the Knight Moves',
                'category': 'Piece Basics',
                'sort_order': 4,
                'learning_objective': 'Student can move the knight in L-shapes, understands jumping ability, and recognizes knight patterns.',
                'teaching_method': '''1. Start with knight on central square (e4)
2. Draw L-shapes - "2 up, 1 over" or "2 over, 1 up"
3. Show all 8 possible moves from center
4. Demonstrate jumping over pieces
5. Practice from edge/corner positions
6. Use "knight tour" exercises''',
                'practice_activities': '''• "Knight adventure" - visit all marked squares
• Obstacle jumping course
• "Minimum moves" - knight to reach target square
• Knight vs pawns mini-games''',
                'pass_criteria': '''• Demonstrates correct L-shaped moves 8/8 times from center
• Shows knight can jump over pieces
• Successfully completes 3-move knight tour
• Identifies when knight can't move (edge limitations)''',
                'enhancement_activities': '''• Knight forks introduction
• "Knight on the rim is grim" principle
• Basic knight endgames
• Centralization concepts''',
                'common_mistakes': '''• Moving like other pieces (diagonal, straight)
• Counting squares incorrectly
• Not utilizing jumping ability
• Confusion about L-shape variations''',
                'estimated_time_min': 15,
                'estimated_time_max': 30,
                'elo_points': 20
            },
            {
                'name': 'How the Queen Moves',
                'category': 'Piece Basics',
                'sort_order': 5,
                'learning_objective': 'Student understands queen combines rook and bishop movement, recognizes queen power and value.',
                'teaching_method': '''1. Place queen in center (d4)
2. Show it combines rook + bishop movement
3. Demonstrate all 8 directions
4. Discuss queen's high value (9 points)
5. Show common queen vs other pieces scenarios
6. Practice "queen hunt" games''',
                'practice_activities': '''• "Queen cleanup" - capture scattered pieces
• Value comparison exercises
• "Queen vs army" scenarios
• Queen and king coordination practice''',
                'pass_criteria': '''• Moves queen in all 8 directions correctly
• Explains queen = rook + bishop movement
• Demonstrates queen's capture power
• Shows understanding of queen's high value''',
                'enhancement_activities': '''• Early queen development problems
• Queen vs rook endgames
• Basic checkmate with queen and king
• Queen sacrifice concepts''',
                'common_mistakes': '''• Moving like a knight
• Underestimating queen's value in trades
• Developing queen too early
• Not utilizing queen's full range''',
                'estimated_time_min': 12,
                'estimated_time_max': 25,
                'elo_points': 15
            },
            {
                'name': 'How the King Moves',
                'category': 'Piece Basics',
                'sort_order': 6,
                'learning_objective': 'Student understands king movement, safety concepts, and basic king activity principles.',
                'teaching_method': '''1. Place king in safe central position
2. Show one-square movement in all directions
3. Emphasize king safety - can't move into check
4. Practice king walks across empty board
5. Demonstrate king capturing (when safe)
6. Introduce basic king safety principles''',
                'practice_activities': '''• "King journey" - safely navigate to target
• "Avoid the danger" - identify unsafe squares
• King vs king exercises
• Basic king safety scenarios''',
                'pass_criteria': '''• Moves king one square in all 8 directions
• Identifies safe vs unsafe squares for king
• Demonstrates king can capture when safe
• Shows understanding king can't move into check''',
                'enhancement_activities': '''• King activity in endgames
• Centralization concepts
• Opposition basics
• King and pawn vs king''',
                'common_mistakes': '''• Moving more than one square
• Moving into check
• Neglecting king safety
• Passivity when king should be active''',
                'estimated_time_min': 10,
                'estimated_time_max': 20,
                'elo_points': 15
            },
            
            # Special Moves Category
            {
                'name': 'Castling Rules',
                'category': 'Special Moves',
                'sort_order': 7,
                'learning_objective': 'Student understands when and how to castle, recognizes castling benefits for king safety.',
                'teaching_method': '''1. Set up position where castling is legal
2. Show kingside castling (short castling) first
3. Demonstrate queenside castling (long castling)
4. Explain castling conditions (king/rook haven't moved, no pieces between, not in check)
5. Practice identifying when castling is legal/illegal
6. Discuss king safety benefits''',
                'practice_activities': '''• "Can you castle?" position quiz
• Set up castling puzzles
• Compare king safety before/after castling
• Racing to castle games''',
                'pass_criteria': '''• Executes kingside castling correctly 3/3 times
• Executes queenside castling correctly 2/2 times
• Identifies 5/5 positions where castling is illegal
• Explains king safety benefit of castling''',
                'enhancement_activities': '''• Castling timing in openings
• Opposite-side castling concepts
• Castling rights and move order
• Attacking the castled position''',
                'common_mistakes': '''• Castling when in check
• Castling through check
• Castling after king has moved
• Wrong move order (king first vs both together)''',
                'estimated_time_min': 15,
                'estimated_time_max': 25,
                'elo_points': 20
            },
            {
                'name': 'Pawn Promotion',
                'category': 'Special Moves',
                'sort_order': 8,
                'learning_objective': 'Student understands promotion rules, can choose appropriate promotion piece, recognizes promotion power.',
                'teaching_method': '''1. Set up pawn one move from promotion
2. Demonstrate promotion to queen (most common)
3. Show underpromotion options (rook, bishop, knight)
4. Explain when underpromotion might be useful
5. Practice promotion scenarios
6. Discuss promotion's game-changing power''',
                'practice_activities': '''• "Race to promote" pawn games
• Promotion choice scenarios
• "Queen vs army" after promotion
• Underpromotion puzzle positions''',
                'pass_criteria': '''• Successfully promotes pawn to queen 3/3 times
• Demonstrates at least one underpromotion
• Explains why queen is usually best choice
• Recognizes promotion opportunity in games''',
                'enhancement_activities': '''• Stalemate tricks with promotion
• Knight promotion tactics
• Promotion in endgames
• Advanced promotion patterns''',
                'common_mistakes': '''• Forgetting promotion is mandatory
• Always choosing queen without thinking
• Not recognizing promotion opportunities
• Confusing promotion rules''',
                'estimated_time_min': 10,
                'estimated_time_max': 20,
                'elo_points': 15
            },
            
            # Game Rules Category
            {
                'name': 'Check and Checkmate',
                'category': 'Game Rules',
                'sort_order': 9,
                'learning_objective': 'Student recognizes check, understands checkmate as game end, can execute basic checkmates.',
                'teaching_method': '''1. Demonstrate check - king under attack
2. Show three ways to get out of check (move, block, capture)
3. Explain checkmate - king in check with no escape
4. Practice basic back-rank checkmate
5. Show queen and king vs king checkmate
6. Distinguish checkmate from stalemate''',
                'practice_activities': '''• "Escape from check" exercises
• Simple checkmate patterns
• "Checkmate in 1" puzzles
• Recognition drills (check vs checkmate vs safe)''',
                'pass_criteria': '''• Identifies check in 5/5 positions
• Demonstrates all three ways to escape check
• Executes queen + king checkmate within 10 moves
• Distinguishes checkmate from stalemate''',
                'enhancement_activities': '''• Two rook checkmate
• Rook and king checkmate
• Common checkmate patterns
• Checkmate with minor pieces''',
                'common_mistakes': '''• Moving into check when trying to escape
• Not recognizing when in check
• Confusing checkmate with stalemate
• Giving up material unnecessarily to escape check''',
                'estimated_time_min': 20,
                'estimated_time_max': 30,
                'elo_points': 25
            },
            {
                'name': 'Stalemate Rules',
                'category': 'Game Rules',
                'sort_order': 10,
                'learning_objective': 'Student understands stalemate as a draw, can recognize stalemate positions, avoids accidental stalemate.',
                'teaching_method': '''1. Set up basic stalemate position (king with no legal moves, not in check)
2. Compare with checkmate (in check vs not in check)
3. Show how stalemate is a draw, not a win
4. Practice recognizing stalemate vs checkmate
5. Demonstrate how to avoid stalemate when winning
6. Show stalemate as defensive resource''',
                'practice_activities': '''• "Stalemate or checkmate?" position quiz
• Avoiding stalemate when winning exercises
• Using stalemate as drawing resource
• King and pawn vs king stalemate patterns''',
                'pass_criteria': '''• Correctly identifies stalemate vs checkmate in 5/5 positions
• Explains stalemate = draw, not win/loss
• Demonstrates how to avoid stalemate when ahead
• Recognizes stalemate opportunities when losing''',
                'enhancement_activities': '''• Complex stalemate patterns
• Stalemate tricks and traps
• Perpetual check concepts
• Advanced drawing techniques''',
                'common_mistakes': '''• Confusing stalemate with checkmate
• Accidentally giving stalemate when winning
• Not recognizing stalemate opportunities
• Moving too quickly without checking for stalemate''',
                'estimated_time_min': 15,
                'estimated_time_max': 25,
                'elo_points': 20
            },
            
            # Basic Values Category  
            {
                'name': 'Piece Values',
                'category': 'Basic Values',
                'sort_order': 11,
                'learning_objective': 'Student understands relative piece values and can make good trading decisions.',
                'teaching_method': '''1. Introduce point system: Pawn=1, Knight/Bishop=3, Rook=5, Queen=9
2. Practice counting material on both sides
3. Show good trades vs bad trades
4. Demonstrate when to trade and when not to
5. Practice "would you make this trade?" scenarios
6. Discuss positional vs material advantages''',
                'practice_activities': '''• Material counting exercises
• "Good trade or bad trade?" quiz
• Trading simulation games
• Value comparison puzzles''',
                'pass_criteria': '''• Correctly states piece values (pawn through queen)
• Counts total material for both sides accurately
• Identifies advantageous trades in 4/5 positions
• Explains reasoning for trade decisions''',
                'enhancement_activities': '''• Positional compensation for material
• Exchange sacrifice concepts
• Material vs time trade-offs
• Advanced trading principles''',
                'common_mistakes': '''• Trading good pieces for bad pieces
• Only focusing on material count
• Not considering position in trades
• Fear of any material exchange''',
                'estimated_time_min': 15,
                'estimated_time_max': 25,
                'elo_points': 20
            }
        ]
        
        # Create topics
        for topic_data in topics:
            topic, created = CurriculumTopic.objects.get_or_create(
                level=foundation,
                name=topic_data['name'],
                defaults=topic_data
            )
            if created:
                self.stdout.write(f'Created foundation topic: {topic.name}')

    def create_tactical_topics(self):
        """Tactical Level Topics (600-800 ELO)"""
        tactical = CurriculumLevel.objects.get(name='TACTICAL')
        
        topics = [
            {
                'name': 'Knight Forks',
                'category': 'Basic Tactics',
                'sort_order': 1,
                'learning_objective': 'Student can identify and execute knight fork tactics, recognizes fork patterns.',
                'teaching_method': '''1. Set up basic knight fork (knight attacking king and another piece)
2. Show royal forks (king + queen)
3. Demonstrate family forks (king + queen + rook)
4. Practice setting up knight forks
5. Show defensive methods against forks
6. Pattern recognition exercises''',
                'practice_activities': '''• Knight fork puzzles (mate in 1)
• Setting up fork opportunities
• "Find the fork" pattern recognition
• Defending against forks exercises''',
                'pass_criteria': '''• Identifies knight fork opportunities in 4/5 positions
• Successfully executes knight fork tactic
• Explains why forks work (two attacks, one move)
• Demonstrates basic fork defense''',
                'enhancement_activities': '''• Advanced fork patterns
• Fork combinations with other tactics
• Setting up forks through sacrifices
• Double attack principles''',
                'common_mistakes': '''• Only looking for checks in forks
• Missing defensive moves
• Setting up forks that can be easily avoided
• Not considering opponent's responses''',
                'estimated_time_min': 15,
                'estimated_time_max': 25,
                'elo_points': 25
            },
            # Add more tactical topics here...
        ]
        
        for topic_data in topics:
            topic, created = CurriculumTopic.objects.get_or_create(
                level=tactical,
                name=topic_data['name'],
                defaults=topic_data
            )
            if created:
                self.stdout.write(f'Created tactical topic: {topic.name}')

    def create_strategic_topics(self):
        """Strategic Level Topics (800-1000 ELO)"""
        strategic = CurriculumLevel.objects.get(name='STRATEGIC')
        
        topics = [
            {
                'name': 'Opening Principles',
                'category': 'Opening Strategy',
                'sort_order': 1,
                'learning_objective': 'Student understands and applies basic opening principles: center control, development, king safety.',
                'teaching_method': '''1. Introduce the three opening principles
2. Show center control with pawns and pieces
3. Demonstrate rapid development (knights before bishops)
4. Explain king safety and early castling
5. Show common opening mistakes
6. Practice applying principles in games''',
                'practice_activities': '''• Opening principle checklist games
• "What's wrong with this opening?" exercises
• Speed development challenges
• Opening principle application practice''',
                'pass_criteria': '''• States the three opening principles correctly
• Applies principles in opening play
• Identifies opening mistakes in given positions
• Shows improved opening play in practice games''',
                'enhancement_activities': '''• Specific opening systems
• Opening transpositions
• Advanced opening concepts
• Opening preparation methods''',
                'common_mistakes': '''• Developing same piece multiple times
• Neglecting king safety
• Ignoring center control
• Moving too many pawns early''',
                'estimated_time_min': 20,
                'estimated_time_max': 30,
                'elo_points': 25
            }
        ]
        
        for topic_data in topics:
            topic, created = CurriculumTopic.objects.get_or_create(
                level=strategic,
                name=topic_data['name'],
                defaults=topic_data
            )
            if created:
                self.stdout.write(f'Created strategic topic: {topic.name}')

    def create_advanced_topics(self):
        """Advanced Level Topics (1000-1200 ELO)"""
        advanced = CurriculumLevel.objects.get(name='ADVANCED')
        
        topics = [
            {
                'name': 'Advanced Tactics',
                'category': 'Complex Tactics',
                'sort_order': 1,
                'learning_objective': 'Student recognizes and executes advanced tactical patterns including deflection, decoy, and interference.',
                'teaching_method': '''1. Introduce deflection tactics
2. Show decoy sacrifices
3. Demonstrate interference patterns
4. Practice combination tactics
5. Show tactical vision improvement methods
6. Advanced pattern recognition training''',
                'practice_activities': '''• Advanced tactical puzzles
• Combination finding exercises
• Pattern recognition drills
• Tactical vision training''',
                'pass_criteria': '''• Solves advanced tactics puzzles
• Identifies complex tactical patterns
• Executes multi-move combinations
• Shows improved tactical awareness''',
                'enhancement_activities': '''• Master-level tactical patterns
• Tactical intuition development
• Complex combination analysis
• Advanced calculation methods''',
                'common_mistakes': '''• Stopping calculation too early
• Missing opponent's defensive resources
• Over-complicating simple tactics
• Not double-checking calculations''',
                'estimated_time_min': 25,
                'estimated_time_max': 35,
                'elo_points': 30
            }
        ]
        
        for topic_data in topics:
            topic, created = CurriculumTopic.objects.get_or_create(
                level=advanced,
                name=topic_data['name'],
                defaults=topic_data
            )
            if created:
                self.stdout.write(f'Created advanced topic: {topic.name}')

    def create_mastery_topics(self):
        """Mastery Level Topics (1200+ ELO)"""
        mastery = CurriculumLevel.objects.get(name='MASTERY')
        
        topics = [
            {
                'name': 'Master-Level Strategy',
                'category': 'Master Concepts',
                'sort_order': 1,
                'learning_objective': 'Student understands advanced strategic concepts and can apply master-level thinking.',
                'teaching_method': '''1. Study master games
2. Analyze strategic plans
3. Learn evaluation methods
4. Practice deep calculation
5. Understand positional sacrifices
6. Develop playing style''',
                'practice_activities': '''• Master game analysis
• Strategic planning exercises
• Deep calculation training
• Tournament preparation''',
                'pass_criteria': '''• Demonstrates master-level understanding
• Creates coherent strategic plans
• Shows deep calculation ability
• Applies concepts in tournament play''',
                'enhancement_activities': '''• Grandmaster-level concepts
• Professional tournament preparation
• Opening repertoire development
• Advanced endgame mastery''',
                'common_mistakes': '''• Over-analyzing positions
• Neglecting practical considerations
• Poor time management
• Inconsistent application of principles''',
                'estimated_time_min': 30,
                'estimated_time_max': 45,
                'elo_points': 40
            }
        ]
        
        for topic_data in topics:
            topic, created = CurriculumTopic.objects.get_or_create(
                level=mastery,
                name=topic_data['name'],
                defaults=topic_data
            )
            if created:
                self.stdout.write(f'Created mastery topic: {topic.name}')

    def create_prerequisites(self):
        """Set up learning prerequisites between topics"""
        # Example prerequisites (you can expand this)
        prerequisite_pairs = [
            # All piece movement must be learned before special moves
            ('How the Pawn Moves', 'Pawn Promotion'),
            ('How the King Moves', 'Castling Rules'),
            ('How the Rook Moves', 'Castling Rules'),
            
            # Basic rules before tactics
            ('Check and Checkmate', 'Knight Forks'),
            ('Piece Values', 'Knight Forks'),
            
            # Foundation before strategy
            ('Castling Rules', 'Opening Principles'),
            ('Check and Checkmate', 'Opening Principles'),
        ]
        
        for prereq_name, required_name in prerequisite_pairs:
            try:
                prerequisite = CurriculumTopic.objects.get(name=prereq_name)
                required_for = CurriculumTopic.objects.get(name=required_name)
                
                obj, created = TopicPrerequisite.objects.get_or_create(
                    prerequisite=prerequisite,
                    required_for=required_for,
                    defaults={'is_strict': True}
                )
                
                if created:
                    self.stdout.write(f'Created prerequisite: {prereq_name} → {required_name}')
                    
            except CurriculumTopic.DoesNotExist as e:
                self.stdout.write(f'Warning: Could not create prerequisite - {e}')
