# Understanding verification


# Ranking system
I want you to develop a ranking tool that helps to sort the paper list so that in /fetch, you get the paper list and can use the ranking tool to sort the list. This can be based on the classical suggestion algorithms. In the report, we can include a button so that the user can indicate which paper is `interested` (even without /scan or /read) and the ranking tool can refine the suggestion strategy. Of course, if the reader click /scan or /read, the paper's topic should also gain higher weight.

Think carefully and make a plan. Note that make the ranking tool independent to modulize the code.


# Refine the work flow

We should have a comprehensive revisit of the functions and consider the develop plan. The /scan function should be redefined. Maybe just call it abstract? And it shows the abstract and provide some comment based on the LLM evaluation on the potential highlight. Then, the /read function provides the normal reading for all the paper content. Let's call it /scan. Then, we need /read for detailed reading. This means we should involk some reading method (please search if there are existing methods). My general idea is to (1) First get the background and key points of the paper (from /scan). (2) Based on them, investigate the details of the results. The key question is: How do the authors come to these conclusions? Do anything violate our current knowledge? If yes, which is correct? Should we update our knowledge base? I think we need another tool for detailed study on individual points. 


# Define the user interaction

Need to consider how to take notes after discussing with the user.


# Visualize the knowledge tree

I want a function and a method to visualize the knowledge tree, so that the user can interact and make correction.


===
Finished works

# Refine the fetch process
[Done]

Use filtering to find the relevant papers. Make a list and feed once to the LLM, together with the preference information, so that the LLM can provide a resulted ranked list. May as the LLM for more things.